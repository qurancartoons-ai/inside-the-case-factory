from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from inside_case_factory.utils.files import write_json


BEAT_ID_RE = re.compile(r"^beat_\d{2}$")
ARCHITECTURE_FIELDS = (
    "version", "status", "beats", "research_utilization_audit",
    "unused_high_value_details", "coverage_gaps", "final_reflection",
    "closing_requirements", "supplementary_metadata",
)
ARCHITECTURE_BEAT_FIELDS = (
    "beat_id", "what_happens", "viewer_learns", "why_here",
    "curiosity_forward", "claim_ids", "high_value_details",
)
_STRING_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string"}}
STORY_ARCHITECTURE_SCHEMA = {
    "name": "story_architecture",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": list(ARCHITECTURE_FIELDS),
        "properties": {
            "version": {"type": "integer"}, "status": {"type": "string"},
            "beats": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": list(ARCHITECTURE_BEAT_FIELDS),
                "properties": {
                    "beat_id": {"type": "string", "pattern": "^beat_[0-9]{2}$"},
                    "what_happens": {"type": "string"}, "viewer_learns": {"type": "string"},
                    "why_here": {"type": "string"}, "curiosity_forward": {"type": "string"},
                    "claim_ids": _STRING_ARRAY_SCHEMA, "high_value_details": _STRING_ARRAY_SCHEMA,
                },
            }},
            "research_utilization_audit": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["detail", "claim_ids", "use_or_omit_reason"],
                "properties": {"detail": {"type": "string"}, "claim_ids": _STRING_ARRAY_SCHEMA, "use_or_omit_reason": {"type": "string"}},
            }},
            "unused_high_value_details": _STRING_ARRAY_SCHEMA, "coverage_gaps": _STRING_ARRAY_SCHEMA,
            "final_reflection": {"type": "string"}, "closing_requirements": _STRING_ARRAY_SCHEMA,
            "supplementary_metadata": {"type": "object", "additionalProperties": False, "required": ["notes"], "properties": {"notes": {"type": ["string", "null"]}}},
        },
    },
}


def script_word_targets(target_duration_minutes: float, words_per_minute: float, tolerance_minutes: float) -> dict[str, int]:
    target = max(1, round(target_duration_minutes * words_per_minute))
    minimum = max(1, math.ceil(max(0.0, target_duration_minutes - tolerance_minutes) * words_per_minute))
    maximum = max(minimum, math.floor((target_duration_minutes + tolerance_minutes) * words_per_minute))
    return {"minimum_words": minimum, "target_words": target, "maximum_words": maximum}


def validate_story_architecture(architecture: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(architecture, dict):
        errors.append("Architecture must be a JSON object.")
        architecture = {}
    unknown = sorted(set(architecture) - set(ARCHITECTURE_FIELDS))
    if unknown:
        errors.append(f"Unknown top-level architecture fields: {', '.join(unknown)}.")
    beats = architecture.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats must be a non-empty array of narrative beat objects.")
        beats = []
    ids: list[str] = []
    required_fields = set(ARCHITECTURE_BEAT_FIELDS)
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
        for field in ("what_happens", "viewer_learns", "why_here", "curiosity_forward"):
            if not isinstance(beat.get(field), str): errors.append(f"beats[{index}].{field} must be a string.")
        for field in ("claim_ids", "high_value_details"):
            value = beat.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value): errors.append(f"beats[{index}].{field} must be an array of strings.")
    for field in ("research_utilization_audit", "unused_high_value_details", "coverage_gaps", "closing_requirements"):
        if not isinstance(architecture.get(field), list): errors.append(f"{field} must be an array outside beats.")
    for index, item in enumerate(architecture.get("research_utilization_audit", []) if isinstance(architecture.get("research_utilization_audit"), list) else []):
        if not isinstance(item, dict) or set(item) != {"detail", "claim_ids", "use_or_omit_reason"}:
            errors.append(f"research_utilization_audit[{index}] must contain exactly detail, claim_ids, and use_or_omit_reason.")
        elif not isinstance(item.get("detail"), str) or not isinstance(item.get("use_or_omit_reason"), str) or not isinstance(item.get("claim_ids"), list) or any(not isinstance(value, str) for value in item["claim_ids"]):
            errors.append(f"research_utilization_audit[{index}] has invalid field types.")
    if not isinstance(architecture.get("final_reflection"), str): errors.append("final_reflection must be a string outside beats.")
    metadata = architecture.get("supplementary_metadata")
    if not isinstance(metadata, dict): errors.append("supplementary_metadata must be an object outside beats.")
    elif set(metadata) != {"notes"} or not (isinstance(metadata.get("notes"), str) or metadata.get("notes") is None): errors.append("supplementary_metadata must contain exactly notes as a string or null.")
    return {"version": 1, "valid": not errors, "narrative_beat_ids": ids, "errors": errors}


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
    "dit markeerde het begin van een complexe periode", "maar liefst", "ging eindelijk opnieuw open",
    "toont aan dat", "hand in hand", "een nieuwe standaard", "markeert een keerpunt",
    "dit traject illustreert hoe", "een nieuwe laag veiligheid", "veilig en duurzaam infrastructuurbeheer",
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
DUTCH_UNNATURAL_PATTERNS = (
    (re.compile(r"\bna deze schrapping\b", re.I), "na deze schrapping"),
    (re.compile(r"\bom zo vroeg mogelijke\b", re.I), "om zo vroeg mogelijke"),
    (re.compile(r"\been nieuw toezicht\b", re.I), "een nieuw toezicht"),
    (re.compile(r"\bdie kwetsuur\b", re.I), "die kwetsuur"),
    (re.compile(r"\btransparantie richting\b", re.I), "transparantie richting"),
    (re.compile(r"\bzichtbaar (?:is )?geborgd\b", re.I), "zichtbaar geborgd"),
    (
        re.compile(r"\b(?:twintig|dertig|veertig|vijftig|zestig|zeventig|tachtig|negentig) (?:een|twee|drie|vier|vijf|zes|zeven|acht|negen)\b", re.I),
        "incorrectly_spaced_compound_number",
    ),
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


def _dutch_years(text: str) -> set[int]:
    units = {"een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8, "negen": 9}
    small = {
        "": 0, "een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7,
        "acht": 8, "negen": 9, "tien": 10, "elf": 11, "twaalf": 12, "dertien": 13,
        "veertien": 14, "vijftien": 15, "zestien": 16, "zeventien": 17, "achttien": 18,
        "negentien": 19, "twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50,
        "zestig": 60, "zeventig": 70, "tachtig": 80, "negentig": 90,
    }
    for tens_word, tens_value in list(small.items()):
        if tens_value >= 20 and tens_value % 10 == 0:
            for unit_word, unit_value in units.items():
                small[f"{unit_word}en{tens_word}"] = tens_value + unit_value
                # Also parse the common model misspelling with a duplicated "en" so it cannot hide a wrong year.
                small[f"{unit_word}enen{tens_word}"] = tens_value + unit_value
    years: set[int] = set()
    for match in re.finditer(r"\btwee\s*duizend(?:\s*([a-zë]+))?\b", text.casefold()):
        suffix = (match.group(1) or "").replace("ë", "e")
        if suffix in small:
            years.add(2000 + small[suffix])
    return years


def _dutch_number_words() -> dict[str, int]:
    units = {"een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8, "negen": 9}
    values = {
        "nul": 0, **units, "tien": 10, "elf": 11, "twaalf": 12, "dertien": 13, "veertien": 14,
        "vijftien": 15, "zestien": 16, "zeventien": 17, "achttien": 18, "negentien": 19,
        "twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50, "zestig": 60,
        "zeventig": 70, "tachtig": 80, "negentig": 90, "honderd": 100,
    }
    for tens_word, tens_value in list(values.items()):
        if tens_value >= 20 and tens_value < 100 and tens_value % 10 == 0:
            for unit_word, unit_value in units.items():
                values[f"{unit_word}en{tens_word}"] = tens_value + unit_value
    for value in range(101, 1000):
        hundreds, remainder = divmod(value, 100)
        prefix = "honderd" if hundreds == 1 else next(word for word, number in units.items() if number == hundreds) + "honderd"
        remainder_word = next((word for word, number in values.items() if number == remainder), "")
        if remainder_word:
            values[prefix + remainder_word] = value
    return values


DUTCH_NUMBER_WORDS = _dutch_number_words()


def _concrete_numbers(text: str, *, dutch: bool) -> set[int]:
    values = {int(value.replace(".", "")) for value in re.findall(r"\b\d[\d.]*\b", text)}
    if dutch:
        without_spoken_years = re.sub(r"\btwee\s*duizend(?:\s*[a-zë]+)?\b", " ", text.casefold())
        for token in re.findall(r"\b[a-zà-öø-ÿ]+\b", without_spoken_years):
            normalized = token.replace("ë", "e")
            if normalized in DUTCH_NUMBER_WORDS and DUTCH_NUMBER_WORDS[normalized] >= 2:
                values.add(DUTCH_NUMBER_WORDS[normalized])
    return values


def _internal_capitalized_names(text: str) -> set[str]:
    names: set[str] = set()
    for sentence in _sentences(text):
        words = list(re.finditer(r"\b[\wÀ-ÖØ-öø-ÿ'-]+\b", sentence))
        for match in words[1:]:
            value = match.group(0)
            if value[:1].isupper() and len(value) > 1:
                names.add(value)
    return names


def _supported_capitalized_names(claims: list[dict[str, Any]]) -> set[str]:
    text = " ".join(
        value
        for claim in claims if isinstance(claim, dict)
        for value in [str(claim.get("text", "")), *map(str, claim.get("people", []))]
    )
    return {match.group(0) for match in re.finditer(r"\b[\wÀ-ÖØ-öø-ÿ'-]+\b", text) if match.group(0)[:1].isupper() and len(match.group(0)) > 1}


def factual_lock_issues(text: str, claims: list[dict[str, Any]], language: object = "Nederlands") -> dict[str, list[Any]]:
    approved_claim_text = " ".join(str(claim.get("text", "")) for claim in claims if isinstance(claim, dict))
    approved_year_text = " ".join(
        f"{claim.get('date', '')} {claim.get('text', '')}" for claim in claims if isinstance(claim, dict)
    )
    approved_years = {int(item) for item in re.findall(r"\b(?:19|20)\d{2}\b", approved_year_text)}
    narrated_years = {int(item) for item in re.findall(r"\b(?:19|20)\d{2}\b", text)}
    if _is_dutch(language):
        narrated_years.update(_dutch_years(text))
    return {
        "unsupported_years": sorted(narrated_years - approved_years) if approved_years else [],
        "unsupported_numbers": sorted(
            _concrete_numbers(text, dutch=_is_dutch(language))
            - _concrete_numbers(approved_claim_text, dutch=_is_dutch(language))
        ),
        "unsupported_names": sorted(_internal_capitalized_names(text) - _supported_capitalized_names(claims)),
        "metadata": sorted(set(re.findall(r"\b(?:c\d{3}|beat_\d{2}|source[_ -]?\d+|bron[_ -]?\d+)\b", text, re.I))),
    }


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
    detected_unnatural = sorted({label for pattern, label in DUTCH_UNNATURAL_PATTERNS if pattern.search(text)})
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
    dramatic_questions = [q for q in questions if re.search(r"\b(?:maar waarom|hoe kon|wat als|wie kon|wat gebeurde|wat veroorzaakte|wie wist|zou .* ooit)\b", q, re.I)]
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
    unnatural.extend(detected_unnatural)
    if noun_stacks: unnatural.append("excessive_noun_stacking")
    if parentheticals: unnatural.append("parenthetical_information")
    rejection_reasons: list[str] = []
    if translated: rejection_reasons.append("Translated-English constructions are present.")
    if detected_unnatural: rejection_reasons.append("Unnatural or ungrammatical Dutch phrasing is present.")
    if overdramatic: rejection_reasons.append("Generic or overdramatic documentary phrases are present.")
    if repeated_openings or repeated_templates: rejection_reasons.append("Repeated sentence or transition patterns are present.")
    if repeated_connectors: rejection_reasons.append("Paragraph-opening connectors are repeated.")
    if dramatic_questions: rejection_reasons.append("rhetorical questions are present.")
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
    wpm = float(config.get("words_per_minute", 125))
    tolerance = float(config.get("duration_tolerance", config.get("duration_tolerance_minutes", 1.0)))
    text = str(script.get("narration", ""))
    section_text = "\n\n".join(
        str(section.get("text", "")).strip() for section in script.get("sections", [])
        if isinstance(section, dict) and str(section.get("text", "")).strip()
    )
    narration_section_mismatch = bool(section_text and re.sub(r"\s+", " ", section_text).strip() != re.sub(r"\s+", " ", text).strip())
    word_count = len(text.split())
    duration = word_count / wpm if wpm else 0.0
    target_minutes = float(script.get("target_duration_minutes", 12) or 12)
    word_contract = script_word_targets(target_minutes, wpm, tolerance)
    minimum = word_contract["minimum_words"]
    target = word_contract["target_words"]
    maximum = word_contract["maximum_words"]
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
    represented_claim_ids = {
        str(claim_id)
        for section in script.get("sections", []) if isinstance(section, dict)
        for claim_id in section.get("claim_ids", [])
        if str(claim_id)
    }
    unknown_structured_claim_ids = sorted(represented_claim_ids - claim_ids)
    minimum_claims = math.ceil(target_minutes * float(config.get("minimum_claims_per_minute", 0) or 0))
    minimum_sources = math.ceil(
        target_minutes / 6 * float(config.get("minimum_distinct_sources_per_six_minutes", 0) or 0)
    )
    represented_source_ids = {
        str(source_id)
        for claim in claims if str(claim.get("id")) in represented_claim_ids
        for source_id in claim.get("source_ids", [])
        if str(source_id)
    }
    vague_patterns = (
        r"\ber doken (?:diverse |verschillende )?theorie[eë]n op\b",
        r"\bverschillende rapporten\b", r"\bsommige analyses\b",
        r"\bgetuigen beweerden\b", r"\ber circuleerden getuigenissen\b",
        r"\bsommige deskundigen\b", r"\bvolgens bronnen\b",
    )
    unattributed_vague_phrases = sorted({
        match.group(0) for pattern in vague_patterns for match in re.finditer(pattern, text.casefold())
    })
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
    factual = factual_lock_issues(text, claims, language)
    unsupported_years = factual["unsupported_years"]
    unsupported_numbers = factual["unsupported_numbers"]
    unsupported_names = factual["unsupported_names"]
    narration_metadata = factual["metadata"]
    factual_lock_violations: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        sentence_years = ({int(item) for item in re.findall(r"\b(?:19|20)\d{2}\b", sentence)} | (_dutch_years(sentence) if _is_dutch(language) else set()))
        for value in sorted(sentence_years & set(unsupported_years)):
            factual_lock_violations.append({"category": "unsupported_year", "value": value, "passage": sentence})
        for value in sorted(_concrete_numbers(sentence, dutch=_is_dutch(language)) & set(unsupported_numbers)):
            factual_lock_violations.append({"category": "unsupported_number", "value": value, "passage": sentence})
        for value in sorted(_internal_capitalized_names(sentence) & set(unsupported_names)):
            factual_lock_violations.append({"category": "unsupported_name", "value": value, "passage": sentence})
    failures: list[str] = []
    if not architecture_report["valid"]: failures.append("Story architecture is malformed and cannot be used for script validation.")
    if word_count < minimum: failures.append(f"Script te kort: {word_count} woorden; minimum is {minimum}.")
    if word_count > maximum: failures.append(f"Script te lang: {word_count} woorden; maximum is {maximum}.")
    if duration < required_minute or duration > required_maxute: failures.append(f"Narration duration {duration:.2f} minutes falls outside {required_minute:.2f}-{required_maxute:.2f} minutes.")
    if len(required_beats) and len(required_beats & represented) / len(required_beats) < float(config.get("minimum_story_beat_coverage", 1.0)):
        failures.append(f"Verhaalonderdelen ontbreken: {len(missing_beats)} beat IDs missing.")
    if unknown_beats: failures.append("Unknown script beat IDs are present.")
    if duplicate_beats: failures.append("Duplicate script beat IDs are present.")
    if narration_section_mismatch: failures.append("Narration does not exactly match the ordered section text.")
    if unused_required: failures.append("Belangrijke onderzoeksdetails ontbreken: required details are unused.")
    if style["unsupported_citation_ids"]: failures.append("Unsupported claim IDs are cited.")
    if unknown_structured_claim_ids: failures.append("Script sections contain unsupported claim IDs.")
    if minimum_claims and len(represented_claim_ids & claim_ids) < minimum_claims:
        failures.append(
            f"Onvoldoende concrete feiten voor {target_minutes:g} minuten: "
            f"{len(represented_claim_ids & claim_ids)} gebruikte claims; minimaal {minimum_claims}."
        )
    if minimum_sources and len(represented_source_ids) < minimum_sources:
        failures.append(
            f"Onvoldoende brondiversiteit: {len(represented_source_ids)} gebruikte bronnen; minimaal {minimum_sources}."
        )
    maximum_vague = int(config.get("maximum_unattributed_vague_phrases", 999) or 0)
    if len(unattributed_vague_phrases) > maximum_vague:
        failures.append("Niet-toegeschreven vage formuleringen moeten worden vervangen door concrete namen, onderzoeken of bronnen.")
    if narration_metadata: failures.append("Narration contains claim IDs or metadata.")
    if style["style_violations"]: failures.append("Banned style phrases are present.")
    if unsupported_years: failures.append("Narration contains years not supported by approved claims.")
    if unsupported_numbers: failures.append("Narration contains numbers not supported by approved claims.")
    if unsupported_names: failures.append("Narration contains names not supported by approved claims.")
    failures.extend(style.get("repetitive_transition_count", 0) and ["Repetitive transitions exceed the quality threshold."] or [])
    failures.extend(dutch["language_rejection_reasons"])
    report = {
        "version": 1, "word_count": word_count, "minimum_words": minimum, "target_words": target, "maximum_words": maximum,
        "estimated_duration_minutes": round(duration, 3), "target_duration_minutes": target_minutes, "duration_tolerance": tolerance,
        "architecture_valid": architecture_report["valid"], "architecture_errors": architecture_report["errors"],
        "represented_beat_ids": sorted(required_beats & represented), "missing_beat_ids": missing_beats,
        "unknown_beat_ids": unknown_beats, "duplicate_beat_ids": duplicate_beats,
        "unsupported_claim_ids": sorted(cited - claim_ids), "unused_required_research_details": unused_required,
        "represented_claim_ids": sorted(represented_claim_ids & claim_ids),
        "unknown_structured_claim_ids": unknown_structured_claim_ids,
        "minimum_required_claims": minimum_claims,
        "represented_source_ids": sorted(represented_source_ids),
        "minimum_required_sources": minimum_sources,
        "unattributed_vague_phrases": unattributed_vague_phrases,
        "unsupported_narrated_years": unsupported_years,
        "unsupported_narrated_numbers": unsupported_numbers, "unsupported_narrated_names": unsupported_names,
        "narration_metadata": narration_metadata,
        "narration_section_mismatch": narration_section_mismatch,
        "factual_lock_violations": factual_lock_violations,
        "unused_optional_research_details": architecture.get("unused_high_value_details", []),
        "banned_style_phrases": style["style_violations"], "repetitive_transitions": style.get("repetitive_transition_count", 0),
        "opening_quality": "pass" if not style["weak_opening"] else "fail", "ending_quality": "fail" if style["generic_conclusion"] else "pass",
        **dutch,
        "pass": not failures, "failure_reasons": failures,
    }
    return report
