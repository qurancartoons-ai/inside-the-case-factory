from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from inside_case_factory.utils.files import read_json, write_json


RELEVANCE_MODEL_VERSION = 3
DEFAULT_MEDIA_RELEVANCE_THRESHOLD = 0.35
RUN_QUALITY_EVIDENCE_GRADE = "evidence_grade"
RUN_QUALITY_SAMPLE_OR_DEMO = "sample_or_demo"
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
    if origin in {"wikimedia_commons", "internet_archive", "wikimedia_commons_category"}:
        score = 0.75
    elif origin == "pexels":
        score = 0.45
    else:
        score = 0.5 if asset.get("source_url") else 0.0
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


def _asset_text(asset: dict[str, Any]) -> str:
    return " ".join(str(asset.get(key, "")) for key in ("title", "description", "summary", "transcript", "source_type", "license", "copyright_status", "source_url"))


def _normalize_run_quality_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    return mode if mode in {RUN_QUALITY_EVIDENCE_GRADE, RUN_QUALITY_SAMPLE_OR_DEMO} else RUN_QUALITY_SAMPLE_OR_DEMO


def score_source_policy(asset: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(asset, dict):
        return {
            "source_category": "unknown",
            "source_policy_score": 0.5,
            "source_policy_reason": "No source metadata was available.",
            "archival_priority": False,
            "rights_confidence": 0.3,
            "generic_stock_penalty": 0.0,
            "synthetic_media_penalty": 0.0,
            "preferred_over_asset_ids": [],
        }
    text = _asset_text(asset).casefold()
    rights_status = str(asset.get("rights_status") or asset.get("copyright_status") or "").casefold()
    if any(marker in rights_status for marker in ("public_domain", "cc0", "cc-by", "cc-by-sa", "creative_commons", "open")):
        rights_confidence = 1.0
    elif any(marker in rights_status for marker in ("approved", "licensed", "permission", "cleared")):
        rights_confidence = 0.8
    elif any(marker in rights_status for marker in ("unknown", "", "pending")):
        rights_confidence = 0.4
    else:
        rights_confidence = 0.55
    source_category = str(asset.get("source_category") or asset.get("source_type") or "unknown").casefold()
    if source_category == "generic_stock_footage":
        score = 0.2
        reason = "Generic stock footage is penalized as a weaker documentary source."
        archival_priority = False
        generic_stock_penalty = 0.25
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("ai generated", "ai-generated", "synthetic", "generated by ai", "generative", "deepfake")):
        source_category = "ai_generated_imagery_video"
        score = 0.05
        reason = "AI-generated or synthetic media is deprioritized."
        archival_priority = False
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.4
        rights_confidence = min(rights_confidence, 0.25)
    elif any(marker in text for marker in ("historical photograph", "historical photo", "museum archive", "museum", "library", "university")) and any(marker in text for marker in ("photo", "photograph", "still")):
        source_category = "historical_photographs"
        score = 0.86
        reason = "Historical photographs and stills are treated as strong documentary evidence."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("government", "national archives", "institutional archive", "public domain", "public-domain")):
        source_category = "government_institutional_archive"
        score = 0.88
        reason = "Institutional or government archives are preferred for rights-safe documentary material."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("archival", "archive", "newsreel", "news reel", "historic footage", "historical footage", "raw footage")):
        source_category = "archival_footage"
        score = 0.92
        reason = "Archival or historical footage is strongly preferred for documentary use."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("newspaper", "magazine", "newspaper scan", "magazine scan", "press clipping", "clipping")):
        source_category = "newspaper_magazine_scans"
        score = 0.86
        reason = "Newspaper or magazine scans are treated as strong historical evidence."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("licensed news", "news footage", "news clip", "broadcast")):
        source_category = "licensed_news_footage"
        score = 0.8
        reason = "Licensed news footage is preferred over generic stock."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("documentary still", "documentary stills", "still", "photograph", "photo")):
        source_category = "documentary_stills"
        score = 0.78
        reason = "Documentary stills and photographs are preferred when they match the scene."
        archival_priority = True
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    elif any(marker in text for marker in ("stock", "b-roll", "generic", "city skyline", "office", "road", "street", "building", "overview", "landscape")):
        source_category = "generic_stock_footage"
        score = 0.2
        reason = "Generic stock footage is penalized as a weaker documentary source."
        archival_priority = False
        generic_stock_penalty = 0.25
        synthetic_media_penalty = 0.0
    else:
        source_category = "unknown"
        score = 0.5 + 0.1 * max(0.0, min(1.0, rights_confidence))
        reason = "No strong source-policy signal was detected."
        archival_priority = False
        generic_stock_penalty = 0.0
        synthetic_media_penalty = 0.0
    if source_category == "ai_generated_imagery_video":
        score = max(0.0, score - 0.05 * max(0.0, min(1.0, rights_confidence)))
    elif source_category in {"government_institutional_archive", "archival_footage", "historical_photographs", "newspaper_magazine_scans"} and rights_confidence < 0.7:
        score = max(0.0, score - 0.05)
    score = round(max(0.0, min(1.0, score)), 2)
    return {
        "source_category": source_category,
        "source_policy_score": score,
        "source_policy_reason": reason,
        "archival_priority": archival_priority,
        "rights_confidence": round(rights_confidence, 2),
        "generic_stock_penalty": round(generic_stock_penalty, 2),
        "synthetic_media_penalty": round(synthetic_media_penalty, 2),
        "preferred_over_asset_ids": [],
    }


def _composite_rank_score(topic_score: float, semantic_score: float, source_policy_score: float, *, scene_context_available: bool, scene_match_passed: bool) -> float:
    if scene_context_available:
        score = 0.45 * topic_score + 0.35 * semantic_score + 0.20 * source_policy_score
        if not scene_match_passed:
            score = max(0.0, score - 0.2)
    else:
        score = 0.7 * topic_score + 0.3 * source_policy_score
    return round(max(0.0, min(1.0, score)), 2)


def _scene_gate_context(scene: dict[str, Any], shot: dict[str, Any] | None = None) -> set[str]:
    intent = shot.get("media_intent", {}) if isinstance(shot, dict) else {}
    asset_requirements = scene.get("asset_requirements", {}) if isinstance(scene.get("asset_requirements"), dict) else {}
    values: list[Any] = [
        scene.get("heading", ""),
        scene.get("visual_summary", ""),
        scene.get("media_requirements", ""),
        asset_requirements.get("content_reason", ""),
        *([str(item) for item in scene.get("archival_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("alternative_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("people", []) if str(item).strip()]),
        *([str(item) for item in scene.get("locations", []) if str(item).strip()]),
        *([str(item) for item in scene.get("events", []) if str(item).strip()]),
        *([str(item) for item in scene.get("dates", []) if str(item).strip()]),
        intent.get("subject", ""),
        intent.get("content_reason", ""),
        *([str(item) for item in intent.get("search_terms", []) if str(item).strip()]),
        *([str(item) for item in intent.get("aliases", []) if str(item).strip()]),
    ]
    return _tokens(*values)


def _scene_intent_context(scene: dict[str, Any], shot: dict[str, Any] | None = None) -> set[str]:
    intent = shot.get("media_intent", {}) if isinstance(shot, dict) else {}
    asset_requirements = scene.get("asset_requirements", {}) if isinstance(scene.get("asset_requirements"), dict) else {}
    people_tokens = _tokens(*[str(item) for item in scene.get("people", []) if str(item).strip()])
    values: list[Any] = [
        scene.get("heading", ""),
        scene.get("visual_summary", ""),
        scene.get("media_requirements", ""),
        asset_requirements.get("content_reason", ""),
        *([str(item) for item in scene.get("archival_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("alternative_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("locations", []) if str(item).strip()]),
        *([str(item) for item in scene.get("events", []) if str(item).strip()]),
        *([str(item) for item in scene.get("dates", []) if str(item).strip()]),
        intent.get("subject", ""),
        intent.get("content_reason", ""),
        *([str(item) for item in intent.get("search_terms", []) if str(item).strip()]),
        *([str(item) for item in intent.get("aliases", []) if str(item).strip()]),
    ]
    return {token for token in _tokens(*values) if token not in people_tokens}


def _collect_scene_terms(scene: dict[str, Any], shot: dict[str, Any] | None = None) -> tuple[list[str], set[str], set[str]]:
    intent = shot.get("media_intent", {}) if isinstance(shot, dict) else {}
    asset_requirements = scene.get("asset_requirements", {}) if isinstance(scene.get("asset_requirements"), dict) else {}
    explicit_terms = [
        str(scene.get("heading", "")),
        str(scene.get("visual_summary", "")),
        str(scene.get("media_requirements", "")),
        str(asset_requirements.get("content_reason", "")),
        *([str(item) for item in scene.get("archival_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("alternative_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("people", []) if str(item).strip()]),
        *([str(item) for item in scene.get("locations", []) if str(item).strip()]),
        *([str(item) for item in scene.get("events", []) if str(item).strip()]),
        *([str(item) for item in scene.get("dates", []) if str(item).strip()]),
        str(intent.get("subject", "")),
        str(intent.get("content_reason", "")),
        *([str(item) for item in intent.get("search_terms", []) if str(item).strip()]),
        *([str(item) for item in intent.get("aliases", []) if str(item).strip()]),
    ]
    joined = " ".join(explicit_terms)
    tokens = _tokens(*explicit_terms)
    explicit_required = []
    for value in [
        *([str(item) for item in scene.get("people", []) if str(item).strip()]),
        *([str(item) for item in scene.get("locations", []) if str(item).strip()]),
        *([str(item) for item in scene.get("events", []) if str(item).strip()]),
        *([str(item) for item in scene.get("dates", []) if str(item).strip()]),
        *([str(item) for item in scene.get("archival_media_queries", []) if str(item).strip()]),
        *([str(item) for item in scene.get("alternative_media_queries", []) if str(item).strip()]),
        str(scene.get("media_requirements", "")),
        str(intent.get("content_reason", "")),
    ]:
        cleaned = str(value).strip()
        if cleaned:
            explicit_required.append(cleaned)
    return explicit_required, tokens, set(_tokens(joined))


def score_scene_asset_match(project_root: Path, scene: dict[str, Any], asset: dict[str, Any], *, shot: dict[str, Any] | None = None, provider: Any | None = None) -> dict[str, Any]:
    del project_root, provider
    if not isinstance(scene, dict) or not isinstance(asset, dict):
        return {
            "scene_id": str(scene.get("id") or scene.get("scene_id") or "scene"),
            "asset_id": str(asset.get("id") or ""),
            "semantic_match_score": 0.0,
            "matched_concepts": [],
            "missing_required_concepts": [],
            "generic_visual_penalty": 0.0,
            "mismatch_reasons": ["scene-mismatch"],
            "final_scene_match_passed": False,
            "explanation": "No scene or asset context was available for semantic matching.",
        }
    scene_id = str(scene.get("id") or scene.get("scene_id") or "scene")
    asset_id = str(asset.get("id") or "")
    required_concepts, scene_tokens, scene_context_tokens = _collect_scene_terms(scene, shot)
    asset_text = " ".join(str(asset.get(key, "")) for key in ("title", "description", "summary", "transcript", "source_type"))
    asset_tokens = _tokens(asset_text)
    matched_concepts = sorted([item for item in required_concepts if any(token in asset_tokens for token in _tokens(item))])
    if not matched_concepts:
        matched_concepts = sorted([concept for concept in sorted(scene_context_tokens & asset_tokens) if len(concept) >= 3])
    missing_required_concepts = []
    for concept in required_concepts:
        concept_tokens = _tokens(concept)
        if not concept_tokens or concept_tokens & asset_tokens:
            continue
        missing_required_concepts.append(concept)
    generic_visual_penalty = 0.0
    generic_markers = ["city", "office", "road", "canal", "crowd", "landscape", "skyline", "street", "building", "outdoor", "interior", "overview", "footage", "video"]
    asset_lower = asset_text.casefold()
    if any(marker in asset_lower for marker in generic_markers):
        generic_visual_penalty += 0.2
    if not matched_concepts and any(marker in asset_lower for marker in generic_markers):
        generic_visual_penalty += 0.2
    if not matched_concepts and len(asset_tokens & scene_tokens) <= 2:
        generic_visual_penalty += 0.15
    mismatch_reasons: list[str] = []
    if missing_required_concepts:
        mismatch_reasons.append("scene-mismatch")
    scene_dates = [str(item) for item in scene.get("dates", []) if str(item).strip()]
    if scene_dates:
        asset_date_tokens = [token for token in re.findall(r"\d{4}", asset_text) if token]
        if asset_date_tokens and not any(str(date) in asset_date_tokens for date in scene_dates):
            mismatch_reasons.append("time_period")
    if generic_visual_penalty >= 0.3 and not matched_concepts:
        mismatch_reasons.append("generic_visual")
    overlap_ratio = len(matched_concepts) / max(1, len(required_concepts)) if required_concepts else (len(asset_tokens & scene_tokens) / max(1, len(scene_tokens)))
    broad_topic_overlap = len(scene_tokens & asset_tokens) / max(1, len(scene_tokens))
    semantic_match_score = round(max(0.0, min(1.0, 0.7 * overlap_ratio + 0.3 * broad_topic_overlap)), 2)
    semantic_match_score = round(max(0.0, semantic_match_score - generic_visual_penalty), 2)
    if mismatch_reasons:
        semantic_match_score = round(max(0.0, semantic_match_score - 0.15), 2)
    final_scene_match_passed = semantic_match_score >= 0.55 and not any(reason in {"time_period"} for reason in mismatch_reasons) and not (missing_required_concepts and generic_visual_penalty >= 0.3)
    explanation = "Strong scene-specific match." if final_scene_match_passed else "Scene-specific meaning is weak or mismatched." if mismatch_reasons else "Scene-specific meaning is weak." 
    if missing_required_concepts:
        explanation = f"Missing required cues: {', '.join(missing_required_concepts[:4])}."
    elif generic_visual_penalty > 0.0:
        explanation = "Generic visual framing is not specific enough for this scene."
    elif mismatch_reasons and "time_period" in mismatch_reasons:
        explanation = "The asset suggests a contradictory time period or setting."
    return {
        "scene_id": scene_id,
        "asset_id": asset_id,
        "semantic_match_score": semantic_match_score,
        "matched_concepts": matched_concepts,
        "missing_required_concepts": missing_required_concepts,
        "generic_visual_penalty": round(generic_visual_penalty, 2),
        "mismatch_reasons": list(dict.fromkeys(mismatch_reasons)),
        "final_scene_match_passed": final_scene_match_passed,
        "explanation": explanation,
    }


def validate_scene_asset_gate(
    project_root: Path,
    scenes: list[dict[str, Any]],
    *,
    media_assets: list[dict[str, Any]] | None = None,
    threshold: float | None = None,
    run_quality_mode: str | None = None,
) -> dict[str, Any]:
    effective_threshold = threshold if threshold is not None else media_threshold(project_root)
    workflow = _read(project_root, "workflow.json")
    resolved_mode = _normalize_run_quality_mode(run_quality_mode or workflow.get("run_quality_mode"))
    assets = [item for item in (media_assets or _read(project_root, "media_sources.json").get("assets", [])) if isinstance(item, dict)]
    asset_lookup = {str(item.get("id") or ""): item for item in assets if isinstance(item, dict) and str(item.get("id") or "")}
    if not assets:
        blocked = resolved_mode == RUN_QUALITY_EVIDENCE_GRADE
        return {
            "version": 1,
            "run_quality_mode": resolved_mode,
            "passed": not blocked,
            "results": [
                {
                    "scene_id": str(scene.get("id") or scene.get("scene_id") or "scene"),
                    "passed": not blocked,
                    "accepted_asset_ids": [],
                    "rejected_asset_ids": [],
                    "rejection_reasons": ["no-candidates"],
                    "blocking_reason": "No candidate assets were available for this scene." if blocked else "",
                    "fallback_mode_used": not blocked,
                }
                for scene in scenes
            ],
            "blocked_scene_ids": [str(scene.get("id") or scene.get("scene_id") or "scene") for scene in scenes] if blocked else [],
            "blocking_reason": "No candidate assets were available for the gate." if blocked else "No candidate assets were available; sample/demo fallback is active.",
            "fallback_mode_used": not blocked,
        }
    results: list[dict[str, Any]] = []
    results_context_present: list[bool] = []
    blocked_scene_ids: list[str] = []
    for scene in scenes:
        scene_id = str(scene.get("id") or scene.get("scene_id") or "scene")
        accepted_asset_ids: list[str] = []
        rejected_asset_ids: list[str] = []
        rejection_reasons: list[str] = []
        scene_shots = scene.get("shots", []) if isinstance(scene.get("shots"), list) else []
        scene_context_present = bool(
            scene.get("asset_gate_context")
            or scene.get("media_requirements")
            or scene.get("archival_media_queries")
            or scene.get("alternative_media_queries")
            or any(isinstance(shot, dict) and shot.get("media_intent") for shot in scene_shots)
        )
        if not scene_shots or not any(isinstance(shot, dict) and shot.get("asset") for shot in scene_shots):
            scene_shots = [{"id": f"{scene_id}-shot-{index + 1}", "asset": asset} for index, asset in enumerate(assets)]
        for shot in scene_shots:
            shot_id = str(shot.get("id") or f"{scene_id}-shot")
            selected_asset = shot.get("asset") if isinstance(shot, dict) else None
            if not isinstance(selected_asset, dict):
                continue
            asset_id = str(selected_asset.get("id") or "")
            asset_source = asset_lookup.get(asset_id, selected_asset) if asset_id else selected_asset
            reasons: list[str] = []
            if not bool(asset_source.get("review_eligible", selected_asset.get("review_eligible", False))):
                reasons.append("review_eligible")
            if float(asset_source.get("relevance_score", selected_asset.get("relevance_score", 0)) or 0) < effective_threshold:
                reasons.append("relevance_threshold")
            if bool(asset_source.get("duplicate_of") or asset_source.get("duplicate_confidence") or asset_source.get("duplicate_kind") or selected_asset.get("duplicate_of") or selected_asset.get("duplicate_confidence") or selected_asset.get("duplicate_kind")):
                reasons.append("duplicate")
            if str(asset_source.get("project_slug", selected_asset.get("project_slug", ""))) and str(asset_source.get("project_slug", selected_asset.get("project_slug", ""))) != project_root.name:
                reasons.append("cross_project")
            scene_context = _scene_gate_context(scene, shot if isinstance(shot, dict) else None)
            scene_intent_context = _scene_intent_context(scene, shot if isinstance(shot, dict) else None)
            asset_text = _asset_text(asset_source)
            asset_tokens = _tokens(asset_text)
            source_policy = score_source_policy(asset_source)
            scene_match = score_scene_asset_match(project_root, scene, asset_source, shot=shot if isinstance(shot, dict) else None)
            composite_score = _composite_rank_score(
                float(asset_source.get("relevance_score", selected_asset.get("relevance_score", 0)) or 0),
                float(scene_match.get("semantic_match_score", 0) or 0),
                float(source_policy.get("source_policy_score", 0) or 0),
                scene_context_available=bool(scene_match.get("scene_id") or scene_context_present),
                scene_match_passed=bool(scene_match.get("final_scene_match_passed", False)),
            )
            linked = bool(
                asset_source.get("mapped_scenes")
                or asset_source.get("suggested_scenes")
                or asset_source.get("shot_ids")
                or selected_asset.get("mapped_scenes")
                or selected_asset.get("suggested_scenes")
                or selected_asset.get("shot_ids")
                or str(scene_id) in {str(value) for value in asset_source.get("mapped_scenes", [])}
                or str(scene_id) in {str(value) for value in asset_source.get("suggested_scenes", [])}
                or str(shot_id) in {str(value) for value in asset_source.get("shot_ids", [])}
                or str(scene_id) in {str(value) for value in selected_asset.get("mapped_scenes", [])}
                or str(scene_id) in {str(value) for value in selected_asset.get("suggested_scenes", [])}
                or str(shot_id) in {str(value) for value in selected_asset.get("shot_ids", [])}
            )
            if scene_context and not (asset_tokens & scene_intent_context):
                reasons.append("scene-intent")
            if not scene_match.get("final_scene_match_passed", False):
                reasons.append("scene-match")
            if composite_score < effective_threshold:
                reasons.append("relevance_threshold")
            if not linked:
                reasons.append("scene-linkage")
            fallback_accept = (
                len(assets) == 1
                and not scene_context_present
                and float(asset_source.get("relevance_score", selected_asset.get("relevance_score", 0)) or 0) >= effective_threshold
                and bool(asset_source.get("source_url") or selected_asset.get("source_url"))
                and not bool(asset_source.get("duplicate_of") or asset_source.get("duplicate_confidence") or asset_source.get("duplicate_kind") or selected_asset.get("duplicate_of") or selected_asset.get("duplicate_confidence") or selected_asset.get("duplicate_kind"))
                and not bool(str(asset_source.get("project_slug", selected_asset.get("project_slug", ""))) and str(asset_source.get("project_slug", selected_asset.get("project_slug", ""))) != project_root.name)
                and set(reasons) <= {"scene-intent", "scene-match", "scene-linkage", "relevance_threshold"}
                and (float(source_policy.get("generic_stock_penalty", 0.0) or 0) > 0 or source_policy.get("source_category") == "unknown")
            )
            if reasons and not fallback_accept:
                rejected_asset_ids.append(asset_id)
                rejection_reasons.extend(reasons)
            else:
                accepted_asset_ids.append(asset_id)
        if scene_context_present:
            passed = bool(accepted_asset_ids)
            if not passed:
                blocked_scene_ids.append(scene_id)
            results.append({
                "scene_id": scene_id,
                "passed": passed,
                "accepted_asset_ids": accepted_asset_ids,
                "rejected_asset_ids": rejected_asset_ids,
                "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
                "blocking_reason": f"Scene {scene_id} has no acceptable asset for the current scene-intent gate." if not passed else "",
            })
            results_context_present.append(True)
            continue
        results.append({
            "scene_id": scene_id,
            "passed": bool(accepted_asset_ids),
            "accepted_asset_ids": accepted_asset_ids,
            "rejected_asset_ids": rejected_asset_ids,
            "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
            "blocking_reason": f"Scene {scene_id} has no acceptable asset for the current scene-intent gate." if not accepted_asset_ids else "",
        })
        results_context_present.append(False)
    overall_passed = all(item["passed"] for item in results)
    # Sample/demo fallback only applies when scenes do not carry explicit scene-intent context.
    # When context exists, failures must stay visible to prevent semantic drift.
    if not overall_passed and resolved_mode != RUN_QUALITY_EVIDENCE_GRADE and not any(results_context_present):
        fallback_results = []
        for item in results:
            fallback_results.append({
                **item,
                "passed": True,
                "blocking_reason": "",
                "fallback_mode_used": not bool(item.get("accepted_asset_ids")),
            })
        return {
            "version": 1,
            "run_quality_mode": resolved_mode,
            "passed": True,
            "results": fallback_results,
            "blocked_scene_ids": [],
            "blocking_reason": "Sample/demo fallback active for one or more scenes.",
            "fallback_mode_used": True,
        }
    return {
        "version": 1,
        "run_quality_mode": resolved_mode,
        "passed": overall_passed,
        "results": results,
        "blocked_scene_ids": blocked_scene_ids,
        "blocking_reason": "; ".join(item["blocking_reason"] for item in results if item["blocking_reason"]) if blocked_scene_ids else "All scenes passed the asset gate.",
        "fallback_mode_used": False,
    }


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
    scene_definitions = [item for item in _read(project_root, "scenes.json").get("scenes", []) if isinstance(item, dict)]
    for asset in assets:
        scene_match_results = []
        for scene in scene_definitions:
            scene_match_results.append(score_scene_asset_match(project_root, scene, asset))
        scene_matches = [item for item in scene_match_results if isinstance(item, dict)]
        if scene_matches:
            best_scene_match = max(scene_matches, key=lambda item: item.get("semantic_match_score", 0.0))
            asset["scene_match_result"] = best_scene_match
            asset["semantic_match_score"] = best_scene_match.get("semantic_match_score", 0.0)
            asset["scene_match_passed"] = bool(best_scene_match.get("final_scene_match_passed", False))
            asset["scene_match_explanation"] = best_scene_match.get("explanation", "")
            asset["generic_visual_penalty"] = best_scene_match.get("generic_visual_penalty", 0.0)
        else:
            asset["scene_match_result"] = {}
            asset["semantic_match_score"] = 0.0
            asset["scene_match_passed"] = False
            asset["scene_match_explanation"] = "No scene context available for semantic matching."
            asset["generic_visual_penalty"] = 0.0

    for asset in assets:
        result = topic_relevance(context, asset)
        scene_score = float(asset.get("scene_relevance_score", 0) or 0)
        effective_score = max(float(result["score"] or 0), scene_score) if result["score"] is not None or scene_score else result["score"]
        effective_reason = str(asset.get("relevance_reason", "")) if scene_score > float(result["score"] or 0) else result["reason"]
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
        scene_match_result = asset.get("scene_match_result") if isinstance(asset.get("scene_match_result"), dict) else {}
        scene_match_passed = bool(scene_match_result.get("final_scene_match_passed", False))
        scene_context_available = bool(scene_match_result)
        source_policy = score_source_policy(asset)
        effective_score = _composite_rank_score(
            float(effective_score or 0),
            float(scene_match_result.get("semantic_match_score", 0.0) or 0),
            float(source_policy.get("source_policy_score", 0.0) or 0),
            scene_context_available=scene_context_available,
            scene_match_passed=scene_match_passed,
        )
        eligible = bool(effective_score is not None and effective_score >= threshold and linked and valid_preview and asset.get("source_url") and not duplicate_of and not cross_project and (not scene_context_available or scene_match_passed))
        if not eligible:
            excluded += 1
        asset.update({
            "topic_relevance": effective_score,
            "relevance_score": effective_score,
            "relevance_reason": effective_reason,
            "relevance_matches": result["matched"],
            "relevance_missing": result["missing"],
            "source_reliability": media_source_reliability(asset),
            "rights_status": rights_status(asset),
            "review_eligible": eligible,
            "review_exclusion_reason": "" if eligible else "Niet inhoudelijk gekoppeld, onvoldoende relevant, geen geldige preview/herkomst, duplicaat, of ander project.",
            "relevance_model_version": RELEVANCE_MODEL_VERSION,
            "source_category": source_policy["source_category"],
            "source_policy_score": source_policy["source_policy_score"],
            "source_policy_reason": source_policy["source_policy_reason"],
            "archival_priority": source_policy["archival_priority"],
            "rights_confidence": source_policy["rights_confidence"],
            "generic_stock_penalty": source_policy["generic_stock_penalty"],
            "synthetic_media_penalty": source_policy["synthetic_media_penalty"],
            "preferred_over_asset_ids": [],
        })
        if not recorded_project:
            asset["project_slug"] = project_root.name

    for scene in scene_definitions:
        scene_id = str(scene.get("id") or scene.get("scene_id") or "")
        if not scene_id:
            continue
        candidates = [asset for asset in assets if asset.get("review_eligible") and asset.get("scene_match_passed") and float(asset.get("relevance_score", 0) or 0) >= threshold and (not asset.get("mapped_scenes") and not asset.get("suggested_scenes") and not asset.get("shot_ids") or str(scene_id) in {str(value) for value in asset.get("mapped_scenes", [])} or str(scene_id) in {str(value) for value in asset.get("suggested_scenes", [])} or str(scene_id) in {str(value) for value in asset.get("shot_ids", [])})]
        if len(candidates) < 2:
            continue
        ranked = sorted(candidates, key=lambda item: (
            float(item.get("relevance_score", 0) or 0),
            float(item.get("source_policy_score", 0) or 0),
            float(item.get("semantic_match_score", 0) or 0),
        ), reverse=True)
        winner = ranked[0]
        others = [str(item.get("id")) for item in ranked[1:] if str(item.get("id"))]
        if others and (bool(winner.get("archival_priority")) or float(winner.get("source_policy_score", 0) or 0) >= 0.75):
            winner["preferred_over_asset_ids"] = others

    if media_path.exists():
        write_json(media_path, media_data)
    return {"sources": len(sources_data.get("sources", [])), "assets": len(assets), "excluded_assets": excluded, "duplicate_assets": duplicates}
