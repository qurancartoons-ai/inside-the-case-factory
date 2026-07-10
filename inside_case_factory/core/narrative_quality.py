from __future__ import annotations

import re
from typing import Any


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
    required_beats = {str(beat.get("id")) for beat in architecture.get("beats", []) if isinstance(beat, dict) and beat.get("id")}
    represented = {str(item) for section in script.get("sections", []) if isinstance(section, dict) for item in section.get("beat_ids", [])}
    missing_beats = sorted(required_beats - represented)
    claim_ids = {str(claim.get("id")) for claim in claims}
    cited = set(re.findall(r"c\d{3}", text))
    required_details = [str(item.get("detail")) for item in architecture.get("research_utilization_audit", []) if isinstance(item, dict) and item.get("required") is True]
    unused_required = [detail for detail in required_details if not any(token.casefold() in text.casefold() for token in detail.split() if len(token) > 5)]
    style = check_script(text, claims, architecture)
    failures: list[str] = []
    if word_count < minimum: failures.append(f"Script te kort: {word_count} woorden; minimum is {minimum}.")
    if word_count > maximum: failures.append(f"Script te lang: {word_count} woorden; maximum is {maximum}.")
    if duration < required_minute or duration > required_maxute: failures.append(f"Narration duration {duration:.2f} minutes falls outside {required_minute:.2f}-{required_maxute:.2f} minutes.")
    if len(required_beats) and len(required_beats & represented) / len(required_beats) < float(config.get("minimum_story_beat_coverage", 1.0)):
        failures.append(f"Verhaalonderdelen ontbreken: {len(missing_beats)} beat IDs missing.")
    if unused_required: failures.append("Belangrijke onderzoeksdetails ontbreken: required details are unused.")
    if style["unsupported_citation_ids"]: failures.append("Unsupported claim IDs are cited.")
    if style["style_violations"]: failures.append("Banned style phrases are present.")
    failures.extend(style.get("repetitive_transition_count", 0) and ["Repetitive transitions exceed the quality threshold."] or [])
    report = {
        "version": 1, "word_count": word_count, "minimum_words": minimum, "target_words": target, "maximum_words": maximum,
        "estimated_duration_minutes": round(duration, 3), "target_duration_minutes": target_minutes, "duration_tolerance": tolerance,
        "represented_beat_ids": sorted(required_beats & represented), "missing_beat_ids": missing_beats,
        "unsupported_claim_ids": sorted(cited - claim_ids), "unused_required_research_details": unused_required,
        "unused_optional_research_details": architecture.get("unused_high_value_details", []),
        "banned_style_phrases": style["style_violations"], "repetitive_transitions": style.get("repetitive_transition_count", 0),
        "opening_quality": "pass" if not style["weak_opening"] else "fail", "ending_quality": "fail" if style["generic_conclusion"] else "pass",
        "pass": not failures, "failure_reasons": failures,
    }
    return report
