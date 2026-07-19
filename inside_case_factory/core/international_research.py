from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from inside_case_factory.utils.files import write_json


PROFILES = {
    "madeleine mccann": [("Verenigd Koninkrijk", "English"), ("Portugal", "Português"), ("Duitsland", "Deutsch")],
    "maddie mccann": [("Verenigd Koninkrijk", "English"), ("Portugal", "Português"), ("Duitsland", "Deutsch")],
    "mh370": [("Maleisië", "Bahasa Melayu"), ("Australië", "English"), ("China", "中文")],
    "titanic": [("Verenigd Koninkrijk", "English"), ("Verenigde Staten", "English")],
    "gaza": [("Israël", "עברית"), ("Palestina", "العربية"), ("Verenigde Naties", "English")],
}

INTERNATIONAL_MEDIA = {"bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "afp.com", "ft.com", "theguardian.com", "washingtonpost.com", "nytimes.com", "dw.com", "france24.com"}
BLOCKED_EDITORIAL = ("netflix", "filmvandaag", "movie", "streaming", "entertainment", "celebrity", "top 10", "things you need to know")
COUNTRY_TLDS = {".uk": "Verenigd Koninkrijk", ".pt": "Portugal", ".de": "Duitsland", ".my": "Maleisië", ".au": "Australië", ".cn": "China", ".il": "Israël", ".ps": "Palestina", ".us": "Verenigde Staten", ".nl": "Nederland"}
COUNTRY_LANGUAGES = {country: language for profile in PROFILES.values() for country, language in profile}


def detect_geographic_context(topic: str, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    lowered = topic.casefold()
    selected = next((value for key, value in PROFILES.items() if key in lowered), None)
    if selected is None:
        involved = (plan or {}).get("involved_countries", [])
        selected = [(str(item.get("country")), str(item.get("language") or "English")) for item in involved if isinstance(item, dict) and item.get("country")]
        if not selected:
            locations = [str(item) for item in (plan or {}).get("locations", []) if str(item).strip()]
            selected = [(location, "English") for location in locations] or [("Internationaal", "English")]
    return [{"country": country, "language": language, "target_coverage": 0.75} for country, language in selected]


def build_international_strategy(topic: str, project_language: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    contexts = detect_geographic_context(topic, plan)
    planned_topic = str((plan or {}).get("exact_topic") or "").strip()
    search_topic = planned_topic if len(planned_topic.split()) >= 2 else topic
    query_parts = []
    official = {"English": "official police court government report", "Dutch": "politie rechtbank officieel dossier", "Nederlands": "politie rechtbank officieel dossier", "Português": "polícia tribunal relatório oficial", "Deutsch": "Polizei Gericht offizieller Bericht", "Bahasa Melayu": "polis mahkamah laporan rasmi", "中文": "警方 法院 官方 报告", "עברית": "משטרה בית משפט דוח רשמי", "العربية": "شرطة محكمة تقرير رسمي"}
    for context in contexts:
        language = context["language"]
        query_parts.append(f'("{search_topic}" {official.get(language, official["English"])})')
    return {
        "version": 1, "project_language": project_language, "contexts": contexts,
        "search_languages": list(dict.fromkeys(item["language"] for item in contexts)),
        "combined_query": " OR ".join(query_parts), "source_priority": [1, 2, 3, 4, 5],
        "iteration_policy": {"target_coverage": 0.75, "stop_on_no_new_relevant_information": True, "requires_new_paid_confirmation": True},
    }


def source_tier(item: dict[str, Any]) -> int:
    url = str(item.get("url", "")); domain = urlparse(url).netloc.lower().removeprefix("www.")
    haystack = f"{url} {item.get('title', '')}".casefold()
    if any(token in haystack for token in ("police", "polícia", "polizei", "court", "tribunal", "government", ".gov", "parliament", "parlamento")): return 1
    if domain in INTERNATIONAL_MEDIA or any(domain.endswith(f".{d}") for d in INTERNATIONAL_MEDIA): return 3
    if domain.endswith(".nl"): return 5
    return 2 if any(domain.endswith(tld) for tld in COUNTRY_TLDS if tld != ".nl") else 4


def source_country(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    return next((country for tld, country in COUNTRY_TLDS.items() if domain.endswith(tld)), "Internationaal")


def filter_and_rank_results(results: list[dict[str, Any]], *, allow_entertainment: bool = False) -> list[dict[str, Any]]:
    seen: set[str] = set(); kept = []
    for raw in results:
        item = dict(raw); url = str(item.get("url", "")); key = re.sub(r"^www\.", "", urlparse(url).netloc.lower()) + urlparse(url).path.rstrip("/")
        text = f"{url} {item.get('title', '')}".casefold()
        if key in seen or (not allow_entertainment and any(term in text for term in BLOCKED_EDITORIAL)): continue
        seen.add(key); item.update({"source_tier": source_tier(item), "source_country": source_country(url), "primary_source": source_tier(item) == 1})
        kept.append(item)
    return sorted(kept, key=lambda item: (item["source_tier"], -float(item.get("quality_score", 0)), -float(item.get("score", 0) or 0)))


def analyze_coverage(project_root: Path, strategy: dict[str, Any], sources: list[dict[str, Any]], claims: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for context in strategy.get("contexts", []):
        country = context["country"]
        country_sources = [s for s in sources if s.get("source_country") == country and s.get("relevance_status", "relevant") == "relevant"]
        linked = {str(s.get("id")) for s in country_sources}
        country_claims = [c for c in claims if linked.intersection(map(str, c.get("source_ids", [])))]
        score = min(100, len(country_sources) * 25 + len(country_claims) * 10 + (20 if any(s.get("primary_source") for s in country_sources) else 0))
        rows.append({"country": country, "score": score, "source_count": len(country_sources), "claim_count": len(country_claims), "target": round(float(context.get("target_coverage", .75)) * 100)})
    requires_more = any(row["score"] < row["target"] for row in rows)
    report = {"version": 1, "countries": rows, "requires_more_search": requires_more, "stop_reason": "coverage_sufficient" if not requires_more else "coverage_gaps", "new_paid_confirmation_required": requires_more}
    write_json(project_root / "manifests" / "international_coverage.json", report)
    return report


def enrich_claim_provenance(claims: list[dict[str, Any]], sources: list[dict[str, Any]], project_language: str) -> None:
    by_id = {str(source.get("id")): source for source in sources}
    for claim in claims:
        linked = [by_id[str(sid)] for sid in claim.get("source_ids", []) if str(sid) in by_id]
        strongest = min(linked, key=lambda source: int(source.get("source_tier") or 5), default={})
        claim.update({
            "original_language": claim.get("original_language") or COUNTRY_LANGUAGES.get(strongest.get("source_country"), "unknown"),
            "translated_text": claim.get("translated_text") or claim.get("text", ""), "translation_language": project_language,
            "source_country": strongest.get("source_country") or "Internationaal", "reliability_score": max(0, 100 - int(strongest.get("source_tier") or 5) * 15),
            "primary_source": bool(strongest.get("primary_source")), "independent_source_count": len({urlparse(str(s.get("url", ""))).netloc for s in linked}),
            "most_authoritative_source_id": strongest.get("id", ""), "contradicting_source_ids": claim.get("contradicting_source_ids", []),
        })


def detect_claim_conflicts(claims: list[dict[str, Any]]) -> None:
    """Connect explicit contradiction notes without guessing that mere nuance is a conflict."""
    for claim in claims:
        if not str(claim.get("contradiction_notes", "")).strip():
            continue
        own_sources = set(map(str, claim.get("source_ids", [])))
        conflicts = {str(source_id) for other in claims if other is not claim for source_id in other.get("source_ids", []) if str(source_id) not in own_sources}
        claim["contradicting_source_ids"] = sorted(conflicts)
