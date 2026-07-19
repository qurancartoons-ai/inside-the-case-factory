from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from inside_case_factory.utils.files import read_json, write_json


RELEVANCE_MODEL_VERSION = 2
DEFAULT_MEDIA_RELEVANCE_THRESHOLD = 0.35
STOPWORDS = {
    "about", "algemeen", "and", "een", "en", "for", "het", "over", "the", "van", "voor",
    "with", "zijn", "docu", "documentaire", "death", "dood", "their", "identify", "analyze", "different",
}


def _read(project_root: Path, name: str) -> dict[str, Any]:
    path = project_root / "manifests" / name
    return read_json(path) if path.exists() else {}


def _tokens(*values: Any) -> set[str]:
    text = " ".join(str(value or "") for value in values).casefold()
    return {token for token in re.findall(r"[a-zà-ÿ0-9]{3,}", text) if token not in STOPWORDS}


def project_context(project_root: Path) -> dict[str, Any]:
    project, plan, request = (_read(project_root, name) for name in ("project.json", "research_plan.json", "production_request.json"))
    people = [str(item) for item in plan.get("people", []) if str(item).strip()]
    aliases = list(dict.fromkeys(people + [str(project.get("topic", "")), str(plan.get("exact_topic", ""))]))
    events = [str(item) for item in plan.get("events", []) if str(item).strip()]
    countries = [str(item.get("country")) for item in plan.get("involved_countries", []) if isinstance(item, dict) and item.get("country")]
    dates = [str(item) for item in plan.get("dates", []) if str(item).strip()]
    questions = [str(item) for item in plan.get("factual_questions", []) if str(item).strip()]
    intent = [str(plan.get("requested_focus", "")), str(request.get("prompt", "")), str(plan.get("documentary_angle", ""))]
    return {
        "project_slug": project_root.name, "aliases": aliases, "people": people, "events": events,
        "countries": countries, "dates": dates, "questions": questions, "intent": intent,
        "topic_tokens": _tokens(*aliases, *events, *intent),
    }


def topic_relevance(context: dict[str, Any], item: dict[str, Any], *, content: str = "") -> dict[str, Any]:
    fields = [item.get(key, "") for key in ("title", "summary", "description", "scene_relevance", "transcript", "source_type")]
    text = " ".join(str(value or "") for value in [*fields, content]).strip()
    if len(_tokens(text)) < 2:
        return {"score": None, "reason": "Niet berekend — onvoldoende inhoud beschikbaar.", "matched": [], "missing": ["titel, samenvatting of tekstinhoud"]}
    lowered = text.casefold()
    matched: list[str] = []
    score = 0.0
    people = [person for person in context["people"] if person.casefold() in lowered]
    if people:
        score += 0.55
        matched.extend(people)
    aliases = [alias for alias in context["aliases"] if len(_tokens(alias)) >= 2 and alias.casefold() in lowered]
    if aliases and not people:
        score += 0.45
        matched.extend(aliases)
    topic_matches = context["topic_tokens"] & _tokens(text)
    if topic_matches:
        score += min(0.25, len(topic_matches) * 0.05)
        matched.extend(sorted(topic_matches))
    event_matches = [event for event in context["events"] if _tokens(event) & _tokens(text)]
    if event_matches:
        score += 0.12
        matched.extend(event_matches)
    date_matches = [date for date in context["dates"] if date.casefold() in lowered]
    if date_matches:
        score += 0.05
        matched.extend(date_matches)
    category = str(item.get("source_type", "")).casefold()
    if matched and category in {"official_record", "court_record", "news", "article", "reference"}:
        score += 0.03
    score = round(min(1.0, score), 2)
    missing = []
    if not content and not item.get("summary") and not item.get("transcript"):
        missing.append("volledige tekstinhoud")
    unique_matches = list(dict.fromkeys(matched))[:8]
    reason = f"Match op {', '.join(unique_matches)}." if unique_matches else "Geen inhoudelijke match met het projectonderwerp."
    return {"score": score, "reason": reason, "matched": unique_matches, "missing": missing}


def source_reliability(source: dict[str, Any]) -> dict[str, Any]:
    tier = int(source.get("source_tier") or 5)
    score = {1: 0.95, 2: 0.85, 3: 0.75, 4: 0.55, 5: 0.35}.get(tier, 0.35)
    reason = "Primaire of officiële bron." if tier == 1 else "Redactionele kwaliteitsbron." if tier <= 3 else "Bron vereist extra verificatie."
    return {"score": score, "reason": reason}


def media_source_reliability(asset: dict[str, Any]) -> dict[str, Any]:
    origin = str(asset.get("discovery", {}).get("source", ""))
    score = 0.75 if origin in {"wikimedia_commons", "internet_archive"} else 0.5 if asset.get("source_url") else 0.0
    return {"score": score, "reason": "Herkomst en metadata aanwezig." if score else "Herkomst ontbreekt."}


def rights_status(item: dict[str, Any]) -> dict[str, str]:
    status = str(item.get("copyright_status") or "unknown")
    return {"status": status, "reason": str(item.get("license") or item.get("license_notes") or "Geen rechteninformatie beschikbaar.")}


def international_coverage(context: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    haystack = f"{source.get('url', '')} {source.get('publisher', '')} {source.get('source_country', '')}".casefold()
    countries = [country for country in context["countries"] if country.casefold() in haystack]
    if "united states" in [country.casefold() for country in context["countries"]] and ("fbi.gov" in haystack or ".gov" in haystack):
        countries.append("United States")
    countries = list(dict.fromkeys(countries))
    return {"score": 1.0 if countries else 0.0, "countries": countries, "reason": f"Draagt bij aan {', '.join(countries)}." if countries else "Geen aantoonbare bijdrage aan een doelland."}


def media_threshold(project_root: Path) -> float:
    config = _read(project_root, "provider_config.json")
    return max(0.0, min(1.0, float(config.get("media_review_minimum_relevance", DEFAULT_MEDIA_RELEVANCE_THRESHOLD))))


def rebuild_relevance_cache(project_root: Path) -> dict[str, int]:
    context = project_context(project_root)
    snapshots = {str(item.get("source_id")): str(item.get("content", "")) for item in _read(project_root, "source_snapshots.json").get("snapshots", []) if isinstance(item, dict)}
    sources_path = project_root / "manifests" / "sources.json"
    sources_data = _read(project_root, "sources.json")
    for source in sources_data.get("sources", []):
        if not isinstance(source, dict):
            continue
        result = topic_relevance(context, source, content=snapshots.get(str(source.get("id")), ""))
        is_duplicate = source.get("relevance_status") == "duplicate"
        source.update({"topic_relevance": result["score"], "relevance_score": result["score"], "relevance_reason": result["reason"], "relevance_matches": result["matched"], "relevance_missing": result["missing"], "source_reliability": source_reliability(source), "international_coverage": international_coverage(context, source), "relevance_model_version": RELEVANCE_MODEL_VERSION})
        source["duplicate_confidence"] = 1.0 if is_duplicate else 0.0
        source["relevance_status"] = "duplicate" if is_duplicate else "not_calculated" if result["score"] is None else "relevant" if result["score"] >= 0.35 else "irrelevant"
    if sources_path.exists():
        write_json(sources_path, sources_data)
    claims = [item for item in _read(project_root, "claims.json").get("claims", []) if isinstance(item, dict)]
    coverage_rows = []
    for country in context["countries"]:
        country_sources = [source for source in sources_data.get("sources", []) if country in source.get("international_coverage", {}).get("countries", []) and (source.get("topic_relevance") or 0) >= 0.35]
        source_ids = {str(source.get("id")) for source in country_sources}
        country_claims = [claim for claim in claims if source_ids.intersection(map(str, claim.get("source_ids", [])))]
        coverage_rows.append({"country": country, "score": min(100, len(country_sources) * 35 + len(country_claims) * 15 + (20 if any(source.get("primary_source") for source in country_sources) else 0)), "source_count": len(country_sources), "claim_count": len(country_claims), "target": 75})
    coverage_path = project_root / "manifests" / "international_coverage.json"
    if coverage_path.exists() and coverage_rows:
        write_json(coverage_path, {"version": 2, "countries": coverage_rows, "requires_more_search": any(row["score"] < row["target"] for row in coverage_rows), "score_component": "international_coverage"})

    media_path = project_root / "manifests" / "media_sources.json"
    media_data = _read(project_root, "media_sources.json")
    assets = [item for item in media_data.get("assets", []) if isinstance(item, dict)]
    seen_hashes: dict[str, str] = {}
    excluded = duplicates = 0
    threshold = media_threshold(project_root)
    for asset in assets:
        result = topic_relevance(context, asset)
        recorded_project = str(asset.get("project_slug", ""))
        cross_project = bool(recorded_project and recorded_project != project_root.name)
        sha = str(asset.get("sha256", ""))
        duplicate_of = seen_hashes.get(sha, "") if sha else ""
        if sha and not duplicate_of:
            seen_hashes[sha] = str(asset.get("id", ""))
        if duplicate_of:
            duplicates += 1
            asset.update({"duplicate_of": duplicate_of, "duplicate_kind": "exact", "duplicate_confidence": 1.0})
        else:
            asset["duplicate_confidence"] = 0.0
        linked = bool(asset.get("mapped_scenes") or asset.get("suggested_scenes") or asset.get("claim_ids") or asset.get("research_question_ids"))
        preview = project_root / str(asset.get("path", ""))
        valid_preview = bool(asset.get("preview_url") or preview.is_file())
        eligible = bool(result["score"] is not None and result["score"] >= threshold and linked and valid_preview and asset.get("source_url") and not duplicate_of and not cross_project)
        if not eligible:
            excluded += 1
        asset.update({"topic_relevance": result["score"], "relevance_score": result["score"], "relevance_reason": result["reason"], "relevance_matches": result["matched"], "relevance_missing": result["missing"], "source_reliability": media_source_reliability(asset), "rights_status": rights_status(asset), "review_eligible": eligible, "review_exclusion_reason": "" if eligible else "Niet inhoudelijk gekoppeld, onvoldoende relevant, geen geldige preview/herkomst, duplicaat, of ander project.", "relevance_model_version": RELEVANCE_MODEL_VERSION})
        if not recorded_project:
            asset["project_slug"] = project_root.name
    if media_path.exists():
        write_json(media_path, media_data)
    return {"sources": len(sources_data.get("sources", [])), "assets": len(assets), "excluded_assets": excluded, "duplicate_assets": duplicates}
