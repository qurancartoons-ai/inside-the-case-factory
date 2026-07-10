from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from inside_case_factory.utils.files import write_json


BEAT_ID_RE = re.compile(r"^beat_\d{2}$")
ARCHITECTURE_FIELDS = {
    "version", "status", "beats", "research_utilization_audit",
    "unused_high_value_details", "coverage_gaps", "final_reflection",
    "closing_requirements", "supplementary_metadata",
}


def validate_story_architecture(architecture: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(architecture, dict):
        errors.append("Architecture must be a JSON object.")
        architecture = {}
    unknown = sorted(set(architecture) - ARCHITECTURE_FIELDS)
    if unknown:
        errors.append(f"Unknown top-level architecture fields: {', '.join(unknown)}.")
    beats = architecture.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats must be a non-empty array of narrative beat objects.")
        beats = []
    ids: list[str] = []
    required_fields = {"beat_id", "what_happens", "viewer_learns", "why_here", "curiosity_forward", "claim_ids", "high_value_details"}
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            errors.append(f"beats[{index}] must be an object, not metadata or a section label.")
            continue
        missing = sorted(required_fields - set(beat))
        extra = sorted(set(beat) - required_fields)
        beat_id = beat.get("beat_id")
        if missing: errors.append(f"beats[{index}] missing fields: {', '.join(missing)}.")
        if extra: errors.append(f"beats[{index}] has unknown fields: {', '.join(extra)}.")
        if not isinstance(beat_id, str) or not BEAT_ID_RE.fullmatch(beat_id):
            errors.append(f"beats[{index}].beat_id must match beat_01, beat_02, etc.")
        else:
            ids.append(beat_id)
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates: errors.append(f"Duplicate beat IDs: {', '.join(duplicates)}.")
    expected = [f"beat_{number:02d}" for number in range(1, len(ids) + 1)]
    if ids and ids != expected:
        errors.append("Narrative beat IDs must be unique, ordered, and contiguous from beat_01.")
    for field in ("research_utilization_audit", "unused_high_value_details", "coverage_gaps", "closing_requirements"):
        if not isinstance(architecture.get(field), list): errors.append(f"{field} must be an array outside beats.")
    if not isinstance(architecture.get("final_reflection"), str): errors.append("final_reflection must be a string outside beats.")
    if not isinstance(architecture.get("supplementary_metadata"), dict): errors.append("supplementary_metadata must be an object outside beats.")
    return {"version": 1, "valid": not errors, "narrative_beat_ids": ids if not duplicates else [], "errors": errors}


def validate_architecture_file(project_root: Path, architecture: Any) -> dict[str, Any]:
    report = validate_story_architecture(architecture)
    write_json(project_root / "manifests" / "story_architecture_validation_report.json", report)
    return report


GENERIC_PHRASES = (
    "this documentary", "in conclusion", "complex weave", "global spectacle", "shattered the calm",
    "chilling revelation", "web of mystery", "what happened next would change everything",
    "haunting questions", "layers of tragedy", "shocking truth", "this case remains a critical lens",
)
ACADEMIC_PHRASES = ("judicial scrutiny", "multifaceted", "underscored the critical importance", "catalyzed discussions")


def check_script(text: str, claims: list[dict[str, Any]], architecture: dict[str, Any]) -> dict[str, Any]:
    lower = text.casefold()
    claim_ids = {str(c.get("id")) for c in claims}
    cited = set(re.findall(r"c\d{3}", text))
    violations = [phrase for phrase in (*GENERIC_PHRASES, *ACADEMIC_PHRASES) if phrase in lower]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    transitions = re.findall(r"\bbut then|\bwhat happened next|\bthere was one problem", lower)
    chronological = any(word in lower for word in ("june 25", "911", "investigation", "trial", "verdict"))
    return {
        "passed": not violations and not (cited - claim_ids) and len(paragraphs) >= 12 and chronological,
        "unsupported_citation_ids": sorted(cited - claim_ids),
        "style_violations": violations,
        "research_summary_risk": len(paragraphs) < 12,
        "repetitive_transition_count": len(transitions),
        "weak_opening": not bool(text[:250].strip()),
        "generic_conclusion": any(phrase in lower[-500:] for phrase in ("in conclusion", "overall", "lesson")),
        "unused_architecture_details": architecture.get("unused_high_value_details", []) if isinstance(architecture, dict) else [],
    }


def validate_script(script: dict[str, Any], claims: list[dict[str, Any]], architecture: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    minimum = int(config.get("minimum_words", 1400))
    target = int(config.get("target_words", 1600))
    maximum = int(config.get("maximum_words", 1900))
    wpm = float(config.get("words_per_minute", 125))
    tolerance = float(config.get("duration_tolerance", config.get("duration_tolerance_minutes", 1.0)))
    text = str(script.get("narration", ""))
    word_count = len(text.split())
    duration = word_count / wpm if wpm else 0.0
    target_minutes = float(script.get("target_duration_minutes", 12) or 12)
    required_minute = target_minutes - tolerance
    required_maxute = target_minutes + tolerance
    architecture_report = validate_story_architecture(architecture)
    required_beats = set(architecture_report["narrative_beat_ids"]) if architecture_report["valid"] else set()
    returned = [str(item) for section in script.get("sections", []) if isinstance(section, dict) for item in section.get("beat_ids", [])]
    represented = set(returned)
    missing_beats = sorted(required_beats - represented)
    unknown_beats = sorted(represented - required_beats)
    duplicate_beats = sorted({item for item in returned if returned.count(item) > 1})
    claim_ids = {str(claim.get("id")) for claim in claims}
    cited = set(re.findall(r"c\d{3}", text))
    required_details = [str(item.get("detail")) for item in architecture.get("research_utilization_audit", []) if isinstance(item, dict) and item.get("required") is True]
    unused_required = [detail for detail in required_details if not any(token.casefold() in text.casefold() for token in detail.split() if len(token) > 5)]
    style = check_script(text, claims, architecture)
    failures: list[str] = []
    if not architecture_report["valid"]: failures.append("Story architecture is malformed and cannot be used for script validation.")
    if word_count < minimum: failures.append(f"Script te kort: {word_count} woorden; minimum is {minimum}.")
    if word_count > maximum: failures.append(f"Script te lang: {word_count} woorden; maximum is {maximum}.")
    if duration < required_minute or duration > required_maxute: failures.append(f"Narration duration {duration:.2f} minutes falls outside {required_minute:.2f}-{required_maxute:.2f} minutes.")
    if len(required_beats) and len(required_beats & represented) / len(required_beats) < float(config.get("minimum_story_beat_coverage", 1.0)):
        failures.append(f"Verhaalonderdelen ontbreken: {len(missing_beats)} beat IDs missing.")
    if unknown_beats: failures.append("Unknown script beat IDs are present.")
    if duplicate_beats: failures.append("Duplicate script beat IDs are present.")
    if unused_required: failures.append("Belangrijke onderzoeksdetails ontbreken: required details are unused.")
    if style["unsupported_citation_ids"]: failures.append("Unsupported claim IDs are cited.")
    if style["style_violations"]: failures.append("Banned style phrases are present.")
    failures.extend(style.get("repetitive_transition_count", 0) and ["Repetitive transitions exceed the quality threshold."] or [])
    report = {
        "version": 1, "word_count": word_count, "minimum_words": minimum, "target_words": target, "maximum_words": maximum,
        "estimated_duration_minutes": round(duration, 3), "target_duration_minutes": target_minutes, "duration_tolerance": tolerance,
        "architecture_valid": architecture_report["valid"], "architecture_errors": architecture_report["errors"],
        "represented_beat_ids": sorted(required_beats & represented), "missing_beat_ids": missing_beats,
        "unknown_beat_ids": unknown_beats, "duplicate_beat_ids": duplicate_beats,
        "unsupported_claim_ids": sorted(cited - claim_ids), "unused_required_research_details": unused_required,
        "unused_optional_research_details": architecture.get("unused_high_value_details", []),
        "banned_style_phrases": style["style_violations"], "repetitive_transitions": style.get("repetitive_transition_count", 0),
        "opening_quality": "pass" if not style["weak_opening"] else "fail", "ending_quality": "fail" if style["generic_conclusion"] else "pass",
        "pass": not failures, "failure_reasons": failures,
    }
    return report
