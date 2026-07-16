from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from inside_case_factory.core.project import slugify
from inside_case_factory.providers.reasoning import ReasoningProvider, ReasoningProviderError
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace


RESEARCH_MANIFESTS = {
    "sources.json": {"version": 1, "sources": []},
    "research.json": {
        "version": 1,
        "provider": "manual",
        "status": "not_started",
        "notes": "",
        "findings": [],
    },
    "timeline.json": {"version": 1, "events": []},
    "claims.json": {"version": 1, "claims": []},
    "source_snapshots.json": {"version": 1, "snapshots": [], "extraction": {}},
    "claim_rejections.json": {"version": 1, "rejections": []},
}


HIGH_AUTHORITY_DOMAINS = {
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "britannica.com",
    "cbsnews.com",
    "cnn.com",
    "latimes.com",
    "npr.org",
    "nytimes.com",
    "reuters.com",
    "theguardian.com",
    "time.com",
    "washingtonpost.com",
    "wikipedia.org",
}

LOW_AUTHORITY_DOMAINS = {
    "dailymail.co.uk",
    "medium.com",
    "quora.com",
    "reddit.com",
    "tiktok.com",
    "vimeo.com",
    "youtube.com",
    "youtu.be",
}

SEO_TITLE_PATTERNS = (
    "10 things",
    "5 things",
    "everything you need to know",
    "what really happened",
    "shocking",
    "ultimate guide",
)


class ResearchProvider:
    name = "base"

    def research(self, project_root: Path, topic: str) -> dict[str, Any]:
        raise NotImplementedError


class ManualResearchProvider(ResearchProvider):
    name = "manual"

    def research(self, project_root: Path, topic: str) -> dict[str, Any]:
        return {
            "provider": self.name,
            "status": "needs_manual_sources",
            "topic": topic,
            "message": (
                "No automated factual research provider is configured. Add source-backed claims manually, "
                "or connect a search/research API provider."
            ),
        }


class TavilyResearchProvider(ResearchProvider):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"
    extract_endpoint = "https://api.tavily.com/extract"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_results: int = 8,
        search_depth: str = "advanced",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self.max_results = max_results
        self.search_depth = search_depth
        self.include_domains = include_domains or []
        self.exclude_domains = exclude_domains or []

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, topic: str, *, content_mode: str = "factual_documentary") -> dict[str, Any]:
        if not self.available:
            return {
                "ok": False,
                "provider": self.name,
                "message": "TAVILY_API_KEY is not set.",
                "results": [],
            }
        query_suffix = {
            "factual_documentary": "official records verified facts reputable reporting",
            "investigative_documentary": "allegations witness accounts contradictions competing explanations unresolved questions",
            "theory_conspiracy": "theory origins proponents alleged motives anomalies counterarguments conventional explanation",
        }.get(content_mode, "official records verified facts reputable reporting")
        payload = {
            "query": f"{topic} {query_suffix}",
            "topic": "general",
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "chunks_per_source": 3,
            "include_answer": False,
            "include_raw_content": True,
            "include_images": False,
            "include_image_descriptions": False,
            "include_domains": self.include_domains,
            "exclude_domains": self.exclude_domains,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "InsideTheCaseFactory/0.1 TavilyResearchProvider",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            return {"ok": False, "provider": self.name, "message": f"Tavily API error {error.code}: {detail}", "results": []}
        except URLError as error:
            return {"ok": False, "provider": self.name, "message": f"Tavily network error: {error}", "results": []}
        results = data.get("results", [])
        if isinstance(results, list):
            results = rank_research_results(results, content_mode=content_mode)
        else:
            results = []
        return {"ok": True, "provider": self.name, "message": "ok", "results": results, "raw": data}

    def extract(self, urls: list[str], *, extract_depth: str = "basic") -> dict[str, Any]:
        selected = [url for url in urls if url][:8]
        if not self.available:
            return {"ok": False, "message": "TAVILY_API_KEY is not set.", "results": [], "failed_results": selected}
        payload = {
            "urls": selected,
            "extract_depth": extract_depth,
            "format": "text",
            "include_images": False,
            "include_usage": True,
        }
        request = Request(
            self.extract_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            return {"ok": False, "message": f"Tavily Extract API error {error.code}: {detail}", "results": [], "failed_results": selected}
        except URLError as error:
            return {"ok": False, "message": f"Tavily Extract network error: {error}", "results": [], "failed_results": selected}
        return {"ok": True, **data}

    def research(
        self,
        project_root: Path,
        topic: str,
        *,
        reasoning_provider: ReasoningProvider | None = None,
        research_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow = load_manifest(project_root, "workflow.json")
        content_mode = str(workflow.get("content_mode", "factual_documentary"))
        result = self.search(topic, content_mode=content_mode)
        ensure_research_manifests(project_root)
        research = load_manifest(project_root, "research.json")
        research.update(
            {
                "provider": self.name,
                "status": "completed" if result.get("ok") else "blocked",
                "topic": topic,
                "message": result.get("message", ""),
                "ran_at": datetime.now(UTC).isoformat(),
            }
        )
        save_manifest(project_root, "research.json", research)
        if not result.get("ok"):
            return result

        added_sources: list[dict[str, Any]] = []
        tavily_results = [item for item in result.get("results", []) if isinstance(item, dict)]
        for item in result.get("results", []):
            if not isinstance(item, dict):
                continue
            source = source_from_tavily_result(project_root, item)
            added_sources.append(source)

        selected = added_sources[:8]
        extraction = self.extract([str(source.get("url", "")) for source in selected], extract_depth="basic")
        snapshots = build_source_snapshots(selected, extraction, tavily_results)
        save_manifest(
            project_root,
            "source_snapshots.json",
            {
                "version": 1,
                "snapshots": snapshots,
                "extraction": {
                    "method": "tavily_extract_basic",
                    "requested_urls": len(selected),
                    "successful_urls": len(snapshots),
                    "estimated_credits": estimate_tavily_extract_credits(len(selected)),
                    "reported_credits": extraction.get("usage", {}).get("credits") if isinstance(extraction.get("usage"), dict) else None,
                    "request_id": extraction.get("request_id", ""),
                },
            },
        )
        mark_source_extraction_status(project_root, snapshots)
        if not snapshots:
            research["status"] = "blocked"
            research["message"] = extraction.get("message", "No selected URL produced usable extracted content.")
            save_manifest(project_root, "research.json", research)
            return {"ok": False, "provider": self.name, "message": research["message"], "sources_added": len(added_sources), "claims_added": 0}

        if reasoning_provider is not None and reasoning_provider.available:
            try:
                analysis = reasoning_provider.analyze_sources(project_root, research_plan or {}, added_sources, snapshots)
            except ReasoningProviderError as error:
                research["status"] = "blocked"
                research["message"] = str(error)
                save_manifest(project_root, "research.json", research)
                return {"ok": False, "provider": self.name, "message": str(error), "sources_added": len(added_sources), "claims_added": 0}
            added_claims, rejections = validate_and_store_claims(project_root, analysis.get("claims", []), snapshots)
            build_validated_research_artifacts(project_root, added_claims)
            return {
                "ok": True,
                "provider": self.name,
                "message": f"Added {len(added_sources)} sources and {len(added_claims)} OpenAI-analyzed pending claims.",
                "sources_added": len(added_sources),
                "claims_added": len(added_claims),
                "claims_rejected": len(rejections),
                "sources_extracted": len(snapshots),
            }

        return {
            "ok": False,
            "provider": self.name,
            "message": "Source extraction completed, but no reasoning provider was available; no claims were created from snippets.",
            "sources_added": len(added_sources),
            "sources_extracted": len(snapshots),
            "claims_added": 0,
        }


BOILERPLATE_PATTERNS = re.compile(
    r"^(accept (all )?cookies|cookie (settings|policy)|advertisement|subscribe|sign in|log in|"
    r"privacy policy|terms of (use|service)|all rights reserved|skip to (content|main)|"
    r"share this|related (stories|articles)|comments?)$",
    re.I,
)


def clean_extracted_text(text: str, *, max_length: int = 18000) -> str:
    kept: list[str] = []
    seen: set[str] = set()
    for raw in text.replace("\r", "\n").split("\n"):
        line = compact_whitespace(re.sub(r"[#*_`>|]+", " ", raw))
        if len(line) < 20 or BOILERPLATE_PATTERNS.match(line):
            continue
        lowered = line.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        kept.append(line)
    return "\n".join(kept)[:max_length].strip()


def estimate_tavily_extract_credits(url_count: int, *, extract_depth: str = "basic") -> int:
    per_five = 2 if extract_depth == "advanced" else 1
    return ((max(0, min(url_count, 8)) + 4) // 5) * per_five


def build_source_snapshots(
    sources: list[dict[str, Any]], extraction: dict[str, Any], search_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    extracted = {str(item.get("url", "")): item for item in extraction.get("results", []) if isinstance(item, dict)}
    fallback = {str(item.get("url", "")): item for item in search_results if isinstance(item, dict)}
    snapshots: list[dict[str, Any]] = []
    for source in sources[:8]:
        url = str(source.get("url", ""))
        item = extracted.get(url, {})
        raw = str(item.get("raw_content") or "")
        method = "tavily_extract_basic"
        if not raw:
            raw = str(fallback.get(url, {}).get("raw_content") or "")
            method = "tavily_search_raw_content_fallback"
        clean = clean_extracted_text(raw)
        if not clean:
            continue
        snapshots.append({
            "source_id": str(source.get("id", "")), "url": url, "status": "success",
            "extracted_at": datetime.now(UTC).isoformat(), "extraction_method": method,
            "content_hash": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
            "content_length": len(clean), "content": clean,
        })
    return snapshots


def mark_source_extraction_status(project_root: Path, snapshots: list[dict[str, Any]]) -> None:
    successful = {str(item.get("source_id")) for item in snapshots}
    document = load_manifest(project_root, "sources.json")
    for source in document.get("sources", []):
        if isinstance(source, dict):
            source["extraction_status"] = "success" if str(source.get("id")) in successful else "failed"
            source["review_status"] = "pending_review"
    save_manifest(project_root, "sources.json", document)


def _find_excerpt(content: str, excerpt: str) -> tuple[int, int] | None:
    exact = content.find(excerpt)
    if exact >= 0:
        return exact, exact + len(excerpt)
    normalized_content = compact_whitespace(content)
    normalized_excerpt = compact_whitespace(excerpt)
    normalized = normalized_content.find(normalized_excerpt)
    return (normalized, normalized + len(normalized_excerpt)) if normalized >= 0 else None


def validate_and_store_claims(
    project_root: Path, proposed: Any, snapshots: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot_by_id = {str(item.get("source_id")): item for item in snapshots}
    from inside_case_factory.core.content_modes import CLAIM_CLASSIFICATIONS, content_mode
    mode = content_mode(load_manifest(project_root, "workflow.json").get("content_mode"))
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    save_manifest(project_root, "claims.json", {"version": 1, "claims": []})
    for index, payload in enumerate(proposed if isinstance(proposed, list) else [], start=1):
        if not isinstance(payload, dict):
            continue
        matches: list[dict[str, Any]] = []
        reasons: list[str] = []
        for evidence in payload.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            source_id = str(evidence.get("source_id", ""))
            excerpt = str(evidence.get("exact_excerpt", "")).strip()
            snapshot = snapshot_by_id.get(source_id)
            location = _find_excerpt(str(snapshot.get("content", "")), excerpt) if snapshot and excerpt else None
            if not location:
                reasons.append(f"Evidence excerpt not found in snapshot for {source_id or 'missing source_id'}")
                continue
            matches.append({"source_id": source_id, "exact_excerpt": excerpt, "start": location[0], "end": location[1], "searchable_text": excerpt})
        source_ids = sorted({item["source_id"] for item in matches})
        classification = str(payload.get("evidence_classification", "verified_fact"))
        if classification not in CLAIM_CLASSIFICATIONS or classification not in mode["claim_classes"]:
            rejected.append({"proposal_index": index, "claim": payload.get("text", ""), "reasons": [f"Evidence classification {classification!r} is not allowed in the selected content mode."]})
            continue
        if not matches or reasons:
            rejected.append({"proposal_index": index, "claim": payload.get("text", ""), "reasons": reasons or ["No verifiable evidence"]})
            continue
        domains = {normalized_domain(str(snapshot_by_id[sid].get("url", ""))) for sid in source_ids}
        hashes = {str(snapshot_by_id[sid].get("content_hash", "")) for sid in source_ids}
        corroboration = "corroborated" if len(domains) >= 2 and len(hashes) >= 2 else "single_source"
        claim = add_claim(
            project_root, text=str(payload.get("text", "")), source_ids=source_ids,
            evidence_classification=classification,
            confidence=str(payload.get("confidence", "needs_review")),
            people=", ".join(map(str, payload.get("people", []))), locations=", ".join(map(str, payload.get("locations", []))),
            events=", ".join(map(str, payload.get("events", []))), date=", ".join(map(str, payload.get("dates", []))),
            evidence_excerpts=[item["exact_excerpt"] for item in matches], evidence=matches,
            research_question_ids=[str(item) for item in payload.get("research_question_ids", []) if str(item)],
            relevance_score=float(payload.get("relevance_score", 0) or 0), source_quality=str(payload.get("source_quality", "")),
            corroboration_status=corroboration, contradiction_notes=str(payload.get("contradiction_notes", "")),
            review_status="pending_review",
        )
        validated.append(claim)
    save_manifest(project_root, "claim_rejections.json", {"version": 1, "rejections": rejected})
    return validated, rejected


def build_validated_research_artifacts(project_root: Path, claims: list[dict[str, Any]]) -> None:
    ids = {str(claim.get("id")) for claim in claims}
    from inside_case_factory.core.content_modes import normalize_content_mode
    workflow = load_manifest(project_root, "workflow.json")
    mode = normalize_content_mode(workflow.get("content_mode"))
    dossier = {
        "version": 1, "status": "validated", "validated_claim_ids": sorted(ids),
        "content_mode": mode,
        "claim_classification_counts": {classification: sum(1 for claim in claims if claim.get("evidence_classification") == classification) for classification in ("verified_fact", "single_source_claim", "allegation", "witness_statement", "official_explanation", "alternative_explanation", "disputed_claim", "interpretation", "speculation", "unanswered_question")},
        "verified_facts": [claim["id"] for claim in claims if claim.get("evidence_classification") == "verified_fact"],
        "attributed_allegations": [claim["id"] for claim in claims if claim.get("evidence_classification") == "allegation"],
        "witness_statements": [claim["id"] for claim in claims if claim.get("evidence_classification") == "witness_statement"],
        "official_explanations": [claim["id"] for claim in claims if claim.get("evidence_classification") == "official_explanation"],
        "alternative_explanations": [claim["id"] for claim in claims if claim.get("evidence_classification") == "alternative_explanation"],
        "disputed_claims": [claim["id"] for claim in claims if claim.get("evidence_classification") == "disputed_claim"],
        "interpretations": [claim["id"] for claim in claims if claim.get("evidence_classification") == "interpretation"],
        "speculation": [claim["id"] for claim in claims if claim.get("evidence_classification") == "speculation"],
        "unanswered_questions": [claim["id"] for claim in claims if claim.get("evidence_classification") == "unanswered_question"],
        "key_facts": [{"claim_id": claim["id"], "statement": claim["text"]} for claim in claims],
        "corroborated_claim_ids": [claim["id"] for claim in claims if claim.get("corroboration_status") == "corroborated"],
        "weak_claim_ids": [claim["id"] for claim in claims if claim.get("confidence") not in {"high", "medium"}],
        "contradictory_claim_ids": [claim["id"] for claim in claims if claim.get("contradiction_notes")],
    }
    events = [{"date": claim.get("date", ""), "claim_id": claim["id"], "summary": claim["text"], "source_ids": claim["source_ids"]} for claim in claims if claim.get("date")]
    save_manifest(project_root, "dossier.json", dossier)
    save_manifest(project_root, "timeline.json", {"version": 1, "events": events, "validated_claim_ids": sorted(ids)})


def source_from_tavily_result(project_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    title = compact_whitespace(str(item.get("title") or item.get("url") or "Untitled source"))
    url = str(item.get("url", ""))
    parsed = urlparse(url)
    publisher = parsed.netloc.removeprefix("www.")
    published = str(item.get("published_date") or item.get("publishedDate") or "")
    source_type = classify_source_type(url, title)
    quality = source_quality(item)
    reliability_notes = compact_whitespace(
        "Retrieved by Tavily automated research. "
        f"Automated source quality: {quality['quality_label']} "
        f"(score {quality['quality_score']}). "
        "Review publisher, original context, and cited claim support before approval."
    )
    return add_source(
        project_root,
        title=title,
        url=url,
        publisher=publisher,
        publication_date=published,
        source_type=source_type,
        reliability_notes=reliability_notes,
    )


def classify_source_type(url: str, title: str) -> str:
    haystack = f"{url} {title}".lower()
    domain = normalized_domain(url)
    if is_official_domain(domain) or "court" in haystack or "coroner" in haystack or "police" in haystack:
        return "official_record"
    if domain_matches(domain, HIGH_AUTHORITY_DOMAINS) and not domain_matches(domain, {"wikipedia.org", "britannica.com"}):
        return "news"
    if domain_matches(domain, {"britannica.com", "wikipedia.org"}):
        return "reference"
    if domain in {"youtube.com", "youtu.be", "vimeo.com", "tiktok.com"}:
        return "video"
    if "blog" in haystack:
        return "blog"
    return "web"


def rank_research_results(results: list[Any], *, content_mode: str = "factual_documentary") -> list[dict[str, Any]]:
    ranked = [annotate_source_quality(item, content_mode=content_mode) for item in results if isinstance(item, dict)]
    return sorted(
        ranked,
        key=lambda item: (
            -float(item.get("quality_score", 0)),
            -float(item.get("score", 0) or 0),
            str(item.get("title", "")),
        ),
    )


def annotate_source_quality(item: dict[str, Any], *, content_mode: str = "factual_documentary") -> dict[str, Any]:
    annotated = dict(item)
    annotated.update(source_quality(item, content_mode=content_mode))
    return annotated


def source_quality(item: dict[str, Any], *, content_mode: str = "factual_documentary") -> dict[str, Any]:
    url = str(item.get("url", ""))
    title = str(item.get("title", ""))
    domain = normalized_domain(url)
    source_type = classify_source_type(url, title)
    tavily_score = float(item.get("score", 0) or 0)
    score = tavily_score * 10
    reasons: list[str] = []

    if source_type == "official_record":
        score += 90
        reasons.append("primary or official record")
    elif source_type == "news":
        score += 70
        reasons.append("reputable news organization")
    elif source_type == "reference":
        score += 55
        reasons.append("established reference source")
    elif source_type == "video":
        score -= 45
        reasons.append("video result requires extra verification")
    elif source_type == "blog":
        score -= 30
        reasons.append("blog source")

    if domain_matches(domain, LOW_AUTHORITY_DOMAINS) and content_mode == "factual_documentary":
        score -= 35
        reasons.append("low-priority platform")
    elif domain_matches(domain, LOW_AUTHORITY_DOMAINS):
        reasons.append("controversial or alternative source retained for mode review")
    if any(pattern in title.lower() for pattern in SEO_TITLE_PATTERNS):
        score -= 25
        reasons.append("SEO-style title")
    if not domain:
        score -= 20
        reasons.append("missing domain")

    label = "high"
    if score < 25:
        label = "low"
    elif score < 60:
        label = "medium"

    return {
        "quality_score": round(score, 3),
        "quality_label": label,
        "source_type": source_type,
        "quality_reasons": reasons or ["general web result"],
    }


def normalized_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_official_domain(domain: str) -> bool:
    return domain.endswith(".gov") or domain.endswith(".mil") or domain.endswith(".edu") or domain.endswith(".court.gov")


def domain_matches(domain: str, candidates: set[str]) -> bool:
    return any(domain == candidate or domain.endswith(f".{candidate}") for candidate in candidates)


def claim_candidates_from_text(text: str, *, limit: int = 4) -> list[str]:
    cleaned = compact_whitespace(re.sub(r"\[[0-9]+\]", "", text))
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    claims: list[str] = []
    for sentence in sentences:
        sentence = compact_whitespace(sentence)
        if len(sentence) < 45 or len(sentence) > 260:
            continue
        if "?" in sentence:
            continue
        if not re.search(r"\b(is|was|were|had|died|said|reported|found|announced|released|ruled|charged|filed)\b", sentence, re.I):
            continue
        claims.append(sentence)
        if len(claims) >= limit:
            break
    return claims


def tavily_config_from_settings(settings: dict[str, Any]) -> TavilyResearchProvider:
    include_domains = settings.get("include_domains", [])
    exclude_domains = settings.get("exclude_domains", [])
    return TavilyResearchProvider(
        max_results=int(settings.get("max_results", 8)),
        search_depth=str(settings.get("search_depth", "advanced")),
        include_domains=[str(item) for item in include_domains] if isinstance(include_domains, list) else [],
        exclude_domains=[str(item) for item in exclude_domains] if isinstance(exclude_domains, list) else [],
    )


def manifest_path(project_root: Path, name: str) -> Path:
    return project_root / "manifests" / name


def ensure_research_manifests(project_root: Path) -> None:
    for name, payload in RESEARCH_MANIFESTS.items():
        path = manifest_path(project_root, name)
        if not path.exists():
            write_json(path, payload)
    workflow = project_root / "manifests" / "workflow.json"
    if not workflow.exists():
        write_json(
            workflow,
            {
                "version": 1,
                "stage": "research",
                "target_duration_minutes": 10,
                "research_approved": False,
                "script_approved": False,
                "scenes_generated": False,
                "voiceover_generated": False,
                "video_rendered": False,
            },
        )


def load_manifest(project_root: Path, name: str) -> dict[str, Any]:
    ensure_research_manifests(project_root)
    data = read_json(manifest_path(project_root, name))
    return data if isinstance(data, dict) else {}


def save_manifest(project_root: Path, name: str, payload: dict[str, Any]) -> None:
    write_json(manifest_path(project_root, name), payload)


def project_topic(project_root: Path) -> str:
    project = load_manifest(project_root, "project.json") if manifest_path(project_root, "project.json").exists() else {}
    return str(project.get("topic", project_root.name))


def add_source(
    project_root: Path,
    *,
    title: str,
    url: str,
    publisher: str = "",
    publication_date: str = "",
    source_type: str = "article",
    reliability_notes: str = "",
) -> dict[str, Any]:
    ensure_research_manifests(project_root)
    sources = load_manifest(project_root, "sources.json")
    source_list = sources.setdefault("sources", [])
    if not isinstance(source_list, list):
        source_list = []
        sources["sources"] = source_list
    source_id = slugify(title or urlparse(url).path or urlparse(url).netloc or "source")
    existing = {str(source.get("id")) for source in source_list if isinstance(source, dict)}
    base = source_id
    counter = 2
    while source_id in existing:
        source_id = f"{base}-{counter}"
        counter += 1
    source = {
        "id": source_id,
        "title": title,
        "url": url,
        "publisher": publisher,
        "publication_date": publication_date,
        "source_type": source_type,
        "access_date": datetime.now(UTC).date().isoformat(),
        "reliability_notes": reliability_notes,
        "review_status": "pending_review",
    }
    source_list.append(source)
    save_manifest(project_root, "sources.json", sources)
    return source


def add_claim(
    project_root: Path,
    *,
    text: str,
    source_ids: list[str],
    evidence_classification: str = "verified_fact",
    confidence: str = "needs_review",
    date: str = "",
    people: str = "",
    locations: str = "",
    events: str = "",
    evidence_excerpts: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    research_question_ids: list[str] | None = None,
    relevance_score: float = 0.0,
    source_quality: str = "",
    corroboration_status: str = "",
    contradiction_notes: str = "",
    review_status: str = "pending_review",
) -> dict[str, Any]:
    ensure_research_manifests(project_root)
    claims = load_manifest(project_root, "claims.json")
    claim_list = claims.setdefault("claims", [])
    if not isinstance(claim_list, list):
        claim_list = []
        claims["claims"] = claim_list
    claim_id = f"c{len(claim_list) + 1:03}"
    claim = {
        "id": claim_id,
        "text": compact_whitespace(text),
        "evidence_classification": evidence_classification,
        "source_ids": source_ids,
        "confidence": confidence,
        "review_status": review_status,
        "date": date,
        "people": split_csv(people),
        "locations": split_csv(locations),
        "events": split_csv(events),
        "evidence_excerpts": evidence_excerpts or [],
        "evidence": evidence or [],
        "research_question_ids": research_question_ids or [],
        "relevance_score": relevance_score,
        "source_quality": source_quality,
        "corroboration_status": corroboration_status,
        "contradiction_notes": contradiction_notes,
    }
    claim_list.append(claim)
    save_manifest(project_root, "claims.json", claims)
    rebuild_timeline(project_root)
    return claim


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def review_item(project_root: Path, manifest_name: str, collection: str, item_id: str, status: str) -> None:
    manifest = load_manifest(project_root, manifest_name)
    items = manifest.get(collection, [])
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and str(item.get("id")) == item_id:
                item["review_status"] = status
                item["reviewed_at"] = datetime.now(UTC).isoformat()
                break
    save_manifest(project_root, manifest_name, manifest)
    if manifest_name == "claims.json":
        rebuild_timeline(project_root)


def approved_sources(project_root: Path) -> list[dict[str, Any]]:
    sources = load_manifest(project_root, "sources.json").get("sources", [])
    return [source for source in sources if isinstance(source, dict) and source.get("review_status") == "approved"]


def approved_claims(project_root: Path) -> list[dict[str, Any]]:
    claims = load_manifest(project_root, "claims.json").get("claims", [])
    return [claim for claim in claims if isinstance(claim, dict) and claim.get("review_status") == "approved"]


def approve_research(project_root: Path) -> bool:
    if not approved_sources(project_root) or not approved_claims(project_root):
        return False
    workflow = load_manifest(project_root, "workflow.json")
    workflow["research_approved"] = True
    workflow["stage"] = "generate_script"
    workflow["research_approved_at"] = datetime.now(UTC).isoformat()
    save_manifest(project_root, "workflow.json", workflow)
    research = load_manifest(project_root, "research.json")
    research["status"] = "approved"
    save_manifest(project_root, "research.json", research)
    return True


def rebuild_timeline(project_root: Path) -> None:
    events = []
    for claim in approved_claims(project_root):
        date = str(claim.get("date", ""))
        if not date:
            continue
        events.append(
            {
                "date": date,
                "claim_id": claim.get("id"),
                "summary": claim.get("text"),
                "source_ids": claim.get("source_ids", []),
                "confidence": claim.get("confidence", ""),
            }
        )
    events.sort(key=lambda item: str(item.get("date", "")))
    save_manifest(project_root, "timeline.json", {"version": 1, "events": events})


def generate_script(
    project_root: Path,
    target_duration_minutes: int = 10,
    *,
    reasoning_provider: ReasoningProvider | None = None,
) -> dict[str, Any]:
    workflow = load_manifest(project_root, "workflow.json")
    if not workflow.get("research_approved"):
        raise RuntimeError("Research must be approved before script generation.")
    claims = approved_claims(project_root)
    if not claims:
        raise RuntimeError("At least one approved claim is required.")

    if reasoning_provider is not None and reasoning_provider.available:
        from inside_case_factory.core.narrative_quality import validate_architecture_file
        architecture_path = project_root / "manifests" / "story_architecture.json"
        architecture = read_json(architecture_path) if architecture_path.exists() else {}
        architecture_report = validate_architecture_file(project_root, architecture)
        if not architecture_report["valid"]:
            raise RuntimeError("Malformed story architecture: " + "; ".join(architecture_report["errors"]))
        research_plan = load_optional_manifest(project_root, "research_plan.json")
        dossier = load_optional_manifest(project_root, "dossier.json")
        language = str(workflow.get("language", research_plan.get("video_language", "English")))
        narrative_outline = reasoning_provider.create_narrative_outline(
            project_root,
            research_plan,
            dossier,
            claims,
            target_duration_minutes,
            language,
        )
        script = reasoning_provider.write_script(
            project_root,
            research_plan,
            dossier,
            narrative_outline,
            claims,
            target_duration_minutes,
            language,
        )
        from inside_case_factory.core.reference_intake import apply_reference_to_script
        script = apply_reference_to_script(project_root, script)
        save_manifest(project_root, "script.json", script)
        workflow["stage"] = "review_script"
        workflow["target_duration_minutes"] = target_duration_minutes
        save_manifest(project_root, "workflow.json", workflow)
        return script

    title = project_topic(project_root)
    ordered = sorted(claims, key=lambda claim: str(claim.get("date", "")))
    paragraphs = [
        f"Opening hook: {title} is a case where the documented record matters more than rumor.",
        "This draft is built only from approved, source-backed claims in the project research file.",
    ]
    for claim in ordered:
        source_refs = ", ".join(str(source_id) for source_id in claim.get("source_ids", []))
        date = f" On {claim['date']}," if claim.get("date") else ""
        paragraphs.append(f"{date} {claim['text']} [sources: {source_refs}]")
    paragraphs.append(
        "Conclusion: The final cut should distinguish verified facts, unresolved questions, and points that require additional sourcing."
    )
    narration = "\n\n".join(compact_whitespace(paragraph) for paragraph in paragraphs)
    script = {
        "version": 1,
        "title": title,
        "target_duration_minutes": target_duration_minutes,
        "status": "draft",
        "generated_from": [claim.get("id") for claim in ordered],
        "opening_hook": paragraphs[0],
        "narration": narration,
        "sections": [
            {
                "id": f"sec{index:02}",
                "claim_ids": [claim.get("id")],
                "text": compact_whitespace(str(claim.get("text", ""))),
            }
            for index, claim in enumerate(ordered, start=1)
        ],
    }
    from inside_case_factory.core.reference_intake import apply_reference_to_script
    script = apply_reference_to_script(project_root, script)
    save_manifest(project_root, "script.json", script)
    workflow["stage"] = "review_script"
    workflow["target_duration_minutes"] = target_duration_minutes
    save_manifest(project_root, "workflow.json", workflow)
    return script


def save_script_edit(project_root: Path, narration: str) -> dict[str, Any]:
    script = load_manifest(project_root, "script.json")
    if not script:
        raise RuntimeError("No script draft exists.")
    script["narration"] = narration
    script["status"] = "edited"
    script["edited_at"] = datetime.now(UTC).isoformat()
    save_manifest(project_root, "script.json", script)
    return script


def approve_script(project_root: Path) -> bool:
    script = load_manifest(project_root, "script.json")
    if not script.get("narration"):
        return False
    script["status"] = "approved"
    script["approved_at"] = datetime.now(UTC).isoformat()
    save_manifest(project_root, "script.json", script)
    workflow = load_manifest(project_root, "workflow.json")
    workflow["script_approved"] = True
    workflow["stage"] = "generate_scenes"
    save_manifest(project_root, "workflow.json", workflow)
    return True


def generate_scenes(
    project_root: Path,
    *,
    reasoning_provider: ReasoningProvider | None = None,
) -> dict[str, Any]:
    workflow = load_manifest(project_root, "workflow.json")
    if not workflow.get("script_approved"):
        raise RuntimeError("Script must be approved before scene generation.")
    script = load_manifest(project_root, "script.json")
    claims = approved_claims(project_root)
    if reasoning_provider is not None and reasoning_provider.available:
        target_minutes = int(workflow.get("target_duration_minutes", script.get("target_duration_minutes", 10)) or 10)
        dossier = load_optional_manifest(project_root, "dossier.json")
        scenes = reasoning_provider.generate_scenes(project_root, script, dossier, claims, target_minutes)
        workflow["scenes_generated"] = True
        workflow["stage"] = "discover_media"
        save_manifest(project_root, "workflow.json", workflow)
        return scenes

    claims_by_id = {str(claim.get("id")): claim for claim in claims}
    narration = str(script.get("narration", ""))
    units = [unit.strip() for unit in re.split(r"\n\s*\n", narration) if unit.strip()]
    if not units:
        units = [narration]
    target_minutes = int(workflow.get("target_duration_minutes", 10))
    total_seconds = target_minutes * 60
    per_scene = max(20, int(total_seconds / max(1, len(units))))
    scenes = []
    ordered_claim_ids = [str(claim_id) for claim_id in script.get("generated_from", [])]
    for index, unit in enumerate(units, start=1):
        claim_id = ordered_claim_ids[min(index - 1, len(ordered_claim_ids) - 1)] if ordered_claim_ids else ""
        claim = claims_by_id.get(claim_id, {})
        people = claim.get("people", []) if isinstance(claim, dict) else []
        locations = claim.get("locations", []) if isinstance(claim, dict) else []
        events = claim.get("events", []) if isinstance(claim, dict) else []
        date = str(claim.get("date", "")) if isinstance(claim, dict) else ""
        query_terms = [project_topic(project_root), *people, *locations, date, *events]
        scenes.append(
            {
                "id": f"s{index:02}",
                "index": index,
                "narration": unit,
                "estimated_duration_seconds": per_scene,
                "claim_ids": [claim_id] if claim_id else [],
                "people": people,
                "locations": locations,
                "dates": [date] if date else [],
                "events": events,
                "archival_media_queries": [compact_whitespace(" ".join(str(term) for term in query_terms if term))],
                "ai_visual_prompt": (
                    "Use only if no approved real media exists: restrained documentary visual based on the approved claim, "
                    "without depicting unverified events."
                ),
            }
        )
    payload = {"version": 1, "status": "draft", "scenes": scenes}
    save_manifest(project_root, "scenes.json", payload)
    workflow["scenes_generated"] = True
    workflow["stage"] = "discover_media"
    save_manifest(project_root, "workflow.json", workflow)
    return payload


def load_optional_manifest(project_root: Path, name: str) -> dict[str, Any]:
    path = manifest_path(project_root, name)
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}
