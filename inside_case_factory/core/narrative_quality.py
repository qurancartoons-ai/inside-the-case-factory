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

DUTCH_OVERDRAMATIC_PHRASES = (
    "wat daarna gebeurde", "niemand kon vermoeden", "de wereld stond op zijn kop",
    "maar achter de schermen", "niets was wat het leek", "een duister geheim",
    "de waarheid zou alles veranderen", "het begin van het einde", "een schokkende wending",
    "de stilte werd doorbroken", "vragen bleven onbeantwoord", "een complex web",
    "stukje bij beetje", "in de schaduw van", "op het eerste gezicht",
    "het verhaal neemt een onverwachte wending",
    "what happened next", "no one could have imagined", "the world was turned upside down",
    "behind the scenes", "nothing was what it seemed", "a dark secret",
    "the truth would change everything", "the beginning of the end", "a shocking twist",
    "the silence was broken", "questions remained unanswered", "a complex web",
    "piece by piece", "in the shadow of", "at first glance", "the story takes an unexpected turn",
)
DUTCH_TRANSLATED_ENGLISH_PATTERNS = (
    (re.compile(r"\bmaak(?:te|t)? (?:zijn|haar|hun) weg naar\b", re.I), "maakte zijn/haar/hun weg naar"),
    (re.compile(r"\baan het einde van de dag\b", re.I), "aan het einde van de dag"),
    (re.compile(r"\bhet feit dat\b", re.I), "het feit dat"),
    (re.compile(r"\bdit is waar\b", re.I), "dit is waar"),
    (re.compile(r"\bhet was niet totdat\b", re.I), "het was niet totdat"),
    (re.compile(r"\bwie weet wat er zou zijn gebeurd\b", re.I), "wie weet wat er zou zijn gebeurd"),
)
DUTCH_ABSTRACT_WORDING = (
    "met betrekking tot", "ten aanzien van", "in het kader van", "dientengevolge",
    "derhalve", "onderhavige", "problematiek", "implementatieproces", "besluitvormingsproces",
)
PARAGRAPH_CONNECTORS = (
    "maar", "toch", "ondertussen", "vervolgens", "echter", "daarna", "bovendien",
    "daarentegen", "intussen", "desondanks", "uiteindelijk",
)


def _is_dutch(language: object) -> bool:
    normalized = str(language or "").strip().casefold()
    return normalized in {"dutch", "nederlands", "nl", "nl-nl", "nl-be"} or normalized.startswith("dutch ")


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if item.strip()]


def analyze_dutch_language(text: str) -> dict[str, Any]:
    lower = text.casefold()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = _sentences(text)
    overdramatic = sorted({phrase for phrase in DUTCH_OVERDRAMATIC_PHRASES if phrase in lower})
    translated = sorted({label for pattern, label in DUTCH_TRANSLATED_ENGLISH_PATTERNS if pattern.search(text)})

    long_sentences = [sentence for sentence in sentences if len(re.findall(r"\b[\w'-]+\b", sentence)) > 32]
    parentheticals = [match.group(0) for match in re.finditer(r"\([^)]{8,}\)", text)]
    citations = [match.group(0) for match in re.finditer(r"(?:\[(?:bron(?:nen)?|source|c\d)|\bvolgens (?:bron|rapport|onderzoek) \d+)", text, re.I)]
    awkward_dates = [match.group(0) for match in re.finditer(r"\b(?:op )?(?:januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december) \d{1,2}(?:e|de|ste)?(?:,? \d{4})?\b", text, re.I)]
    abstract = sorted({phrase for phrase in DUTCH_ABSTRACT_WORDING if phrase in lower})
    noun_stacks = [match.group(0) for match in re.finditer(r"\b(?:[a-zà-öø-ÿ]+(?:ing|iteit|isme|proces|beleid|systeem)\s+){2,}[a-zà-öø-ÿ]+\b", lower)]

    openings: list[str] = []
    connector_openings: list[str] = []
    for paragraph in paragraphs:
        words = re.findall(r"\b[\w'-]+\b", paragraph.casefold())
        if words:
            openings.append(" ".join(words[:3]))
            if words[0] in PARAGRAPH_CONNECTORS:
                connector_openings.append(words[0])
    repeated_openings = sorted({opening for opening in openings if openings.count(opening) > 1})
    repeated_connectors = sorted({item for item in connector_openings if connector_openings.count(item) > 1})

    templates: list[str] = []
    for sentence in sentences:
        words = re.findall(r"\b[\w'-]+\b", sentence.casefold())
        if len(words) >= 5:
            templates.append(" ".join(words[:4]))
    repeated_templates = sorted({template for template in templates if templates.count(template) > 1})
    questions = [sentence for sentence in sentences if sentence.endswith("?")]
    dramatic_questions = [q for q in questions if re.search(r"\b(?:maar waarom|hoe kon|wat als|wie kon|wat gebeurde|wie wist|zou .* ooit)\b", q, re.I)]
    normalized_questions = [re.sub(r"\W+", " ", q.casefold()).strip() for q in dramatic_questions]
    repeated_questions = sorted({q for q in normalized_questions if normalized_questions.count(q) > 1})

    spoken_issues: list[str] = []
    if long_sentences: spoken_issues.append("excessively_long_sentences")
    if noun_stacks: spoken_issues.append("excessive_noun_stacking")
    if abstract: spoken_issues.append("abstract_or_bureaucratic_wording")
    if parentheticals: spoken_issues.append("parenthetical_information")
    if citations: spoken_issues.append("citation_like_phrasing")
    if awkward_dates: spoken_issues.append("unnatural_date_or_number_phrasing")

    unnatural: list[str] = []
    unnatural.extend(abstract)
    unnatural.extend(translated)
    if noun_stacks: unnatural.append("excessive_noun_stacking")
    if parentheticals: unnatural.append("parenthetical_information")
    rejection_reasons: list[str] = []
    if translated: rejection_reasons.append("Translated-English constructions are present.")
    if overdramatic: rejection_reasons.append("Generic or overdramatic documentary phrases are present.")
    if repeated_openings or repeated_templates: rejection_reasons.append("Repeated sentence or transition patterns are present.")
    if repeated_connectors: rejection_reasons.append("Paragraph-opening connectors are repeated.")
    if repeated_questions or len(dramatic_questions) > 1: rejection_reasons.append("Repetitive rhetorical questions are present.")
    if spoken_issues: rejection_reasons.append("The narration contains spoken-language quality issues.")
    return {
        "dutch_language_quality": "pass" if not rejection_reasons else "fail",
        "translated_english_patterns": translated,
        "unnatural_phrasing": sorted(set(unnatural)),
        "repeated_sentence_patterns": sorted(set(repeated_openings + repeated_templates + repeated_questions)),
        "overdramatic_phrases": overdramatic,
        "spoken_language_issues": spoken_issues,
        "long_sentence_count": len(long_sentences),
        "connector_repetition": repeated_connectors,
        "language_rejection_reasons": rejection_reasons,
    }


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
    language = script.get("language", config.get("language", ""))
    dutch = analyze_dutch_language(text) if _is_dutch(language) else {
        "dutch_language_quality": "not_applicable", "translated_english_patterns": [],
        "unnatural_phrasing": [], "repeated_sentence_patterns": [], "overdramatic_phrases": [],
        "spoken_language_issues": [], "long_sentence_count": 0, "connector_repetition": [],
        "language_rejection_reasons": [],
    }
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
    failures.extend(dutch["language_rejection_reasons"])
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
        **dutch,
        "pass": not failures, "failure_reasons": failures,
    }
    return report
