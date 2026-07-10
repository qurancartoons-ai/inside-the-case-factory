from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path
from typing import Any

from inside_case_factory.core.narrative_quality import factual_lock_issues, validate_script
from inside_case_factory.providers.reasoning import ReasoningProvider
from inside_case_factory.utils.files import write_json


MAX_MODEL_CALLS = 3


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if part.strip()]


def _last_section_sentence(script: dict[str, Any]) -> str:
    sections = [section for section in script.get("sections", []) if isinstance(section, dict)]
    for section in reversed(sections):
        sentences = _sentences(str(section.get("text", "")))
        if sentences:
            return sentences[-1]
    sentences = _sentences(str(script.get("narration", "")))
    return sentences[-1] if sentences else ""


def _longest_sentence(script: dict[str, Any]) -> str:
    sentences = _sentences(str(script.get("narration", "")))
    if not sentences:
        return ""
    return max(sentences, key=lambda sentence: len(re.findall(r"\b[\w'-]+\b", sentence)))


def _claim_context(script: dict[str, Any], passage: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = [section for section in script.get("sections", []) if isinstance(section, dict)]
    matching = [section for section in sections if passage in str(section.get("text", ""))]
    if not matching and sections and passage in str(script.get("narration", "")):
        matching = [sections[-1]]
    ids = {str(item) for section in matching for item in section.get("claim_ids", [])}
    return [{"id": claim.get("id"), "text": claim.get("text"), "date": claim.get("date", "")}
            for claim in claims if str(claim.get("id")) in ids]


def _semantic_issue_map(report: dict[str, Any]) -> dict[str, str]:
    issues: dict[str, str] = {}

    word_count = report.get("word_count")
    minimum = report.get("minimum_words")
    maximum = report.get("maximum_words")
    duration = report.get("estimated_duration_minutes")
    target_duration = report.get("target_duration_minutes")
    tolerance = report.get("duration_tolerance")
    if isinstance(word_count, int) and isinstance(minimum, int) and word_count < minimum:
        issues["word_count_shortfall"] = f"Script te kort: {word_count} woorden; minimum is {minimum}."
    if isinstance(word_count, int) and isinstance(maximum, int) and word_count > maximum:
        issues["word_count_excess"] = f"Script te lang: {word_count} woorden; maximum is {maximum}."
    if all(isinstance(value, (int, float)) for value in (duration, target_duration, tolerance)):
        if float(duration) < float(target_duration) - float(tolerance) or float(duration) > float(target_duration) + float(tolerance):
            issues["duration_out_of_range"] = (
                f"Narration duration {float(duration):.2f} minutes falls outside "
                f"{float(target_duration) - float(tolerance):.2f}-{float(target_duration) + float(tolerance):.2f} minutes."
            )

    if not report.get("architecture_valid", True):
        issues["architecture_invalid"] = "Story architecture is malformed and cannot be used for script validation."

    if report.get("narration_section_mismatch"):
        issues["narration_section_mismatch"] = "Narration does not exactly match the ordered section text."

    if report.get("unsupported_claim_ids"):
        for claim_id in report.get("unsupported_claim_ids", []):
            issues[f"unsupported_claim_id:{claim_id}"] = "Unsupported claim IDs are cited."

    if report.get("missing_beat_ids"):
        for beat_id in report.get("missing_beat_ids", []):
            issues[f"missing_beat_id:{beat_id}"] = f"Verhaalonderdeel {beat_id} ontbreekt."

    if report.get("unknown_beat_ids"):
        for beat_id in report.get("unknown_beat_ids", []):
            issues[f"unknown_beat_id:{beat_id}"] = "Unknown script beat IDs are present."

    if report.get("duplicate_beat_ids"):
        for beat_id in report.get("duplicate_beat_ids", []):
            issues[f"duplicate_beat_id:{beat_id}"] = "Duplicate script beat IDs are present."

    if report.get("unused_required_research_details"):
        for detail in report.get("unused_required_research_details", []):
            issues[f"unused_required_detail:{detail}"] = "Belangrijke onderzoeksdetails ontbreken."

    if report.get("narration_metadata"):
        for token in report.get("narration_metadata", []):
            issues[f"narration_metadata:{token}"] = "Narration contains claim IDs or metadata."

    if report.get("banned_style_phrases"):
        for token in report.get("banned_style_phrases", []):
            issues[f"banned_style_phrase:{token}"] = "Banned style phrases are present."

    if report.get("unsupported_narrated_years"):
        for value in report.get("unsupported_narrated_years", []):
            issues[f"unsupported_year:{value}"] = "Narration contains years not supported by approved claims."

    if report.get("unsupported_narrated_numbers"):
        for value in report.get("unsupported_narrated_numbers", []):
            issues[f"unsupported_number:{value}"] = "Narration contains numbers not supported by approved claims."

    if report.get("unsupported_narrated_names"):
        for value in report.get("unsupported_narrated_names", []):
            issues[f"unsupported_name:{value}"] = "Narration contains names not supported by approved claims."

    for value in report.get("translated_english_patterns", []):
        issues[f"translated_english:{value}"] = "Translated-English constructions are present."
    for value in report.get("unnatural_phrasing", []):
        issues[f"unnatural_phrasing:{value}"] = "Unnatural or ungrammatical Dutch phrasing is present."
    for value in report.get("overdramatic_phrases", []):
        issues[f"overdramatic_phrase:{value}"] = "Generic or overdramatic documentary phrases are present."
    for value in report.get("repeated_sentence_patterns", []):
        issues[f"repeated_sentence_pattern:{value}"] = "Repeated sentence or transition patterns are present."
    for value in report.get("connector_repetition", []):
        issues[f"connector_repetition:{value}"] = "Paragraph-opening connectors are repeated."
    for value in report.get("spoken_language_issues", []):
        issues[f"spoken_language_issue:{value}"] = "The narration contains spoken-language quality issues."
    if report.get("long_sentence_count"):
        issues["long_sentence_count"] = "The narration contains spoken-language quality issues."
    if report.get("repetitive_transitions"):
        issues["repetitive_transitions"] = "Repeated sentence or transition patterns are present."

    for reason in report.get("language_rejection_reasons", []):
        if "rhetorical questions" in str(reason).casefold():
            issues["rhetorical_questions"] = "rhetorical questions are present."
        elif "translated-english constructions" in str(reason).casefold():
            issues["translated_english_language"] = "Translated-English constructions are present."
        elif "unnatural or ungrammatical dutch phrasing" in str(reason).casefold():
            issues["unnatural_dutch_language"] = "Unnatural or ungrammatical Dutch phrasing is present."
        elif "generic or overdramatic" in str(reason).casefold():
            issues["overdramatic_language"] = "Generic or overdramatic documentary phrases are present."
        elif "repeated sentence or transition patterns" in str(reason).casefold():
            issues["repeated_patterns_language"] = "Repeated sentence or transition patterns are present."
        elif "paragraph-opening connectors" in str(reason).casefold():
            issues["connector_repetition_language"] = "Paragraph-opening connectors are repeated."
        elif "spoken-language quality issues" in str(reason).casefold():
            issues["spoken_language_rejection"] = "The narration contains spoken-language quality issues."
        else:
            issues[f"language_rejection:{reason}"] = str(reason)

    if report.get("failure_reasons") and not issues:
        for reason in report.get("failure_reasons", []):
            issues[f"reason:{reason}"] = str(reason)
    return issues


def _word_count_repair(script: dict[str, Any], report: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    word_count = int(report.get("word_count", 0) or 0)
    minimum = int(report.get("minimum_words", 0) or 0)
    maximum = int(report.get("maximum_words", 0) or 0)
    if word_count < minimum:
        passage = _last_section_sentence(script)
        if not passage:
            return None
        return {
            "target_id": "repair_word_count",
            "original_passage": passage,
            "validator_errors": [f"word_count_shortfall: {minimum - word_count}"],
            "required_repairs": [
                f"Expand only this passage with approved-claim details until the narration reaches at least {minimum} words.",
                "Preserve every other passage exactly.",
            ],
            "approved_claims": _claim_context(script, passage, claims),
        }
    if word_count > maximum:
        passage = _longest_sentence(script)
        if not passage:
            return None
        return {
            "target_id": "repair_word_count",
            "original_passage": passage,
            "validator_errors": [f"word_count_excess: {word_count - maximum}"],
            "required_repairs": [
                f"Compress only this passage until the narration is at most {maximum} words.",
                "Preserve every other passage exactly.",
            ],
            "approved_claims": _claim_context(script, passage, claims),
        }
    return None


def build_script_repair_plan(
    script: dict[str, Any], report: dict[str, Any], claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create exact passage replacements; never emit a broad rewrite instruction."""
    claims = claims or []
    narration = str(script.get("narration", ""))
    sentences = _sentences(narration)
    targets: dict[str, dict[str, Any]] = {}

    def add(passage: str, error: str, repair: str, *, target_id: str | None = None) -> None:
        if not passage or passage not in narration:
            return
        target = targets.setdefault(passage, {
            "target_id": target_id or f"repair_{len(targets) + 1:02d}",
            "original_passage": passage,
            "validator_errors": [],
            "required_repairs": [],
            "approved_claims": _claim_context(script, passage, claims),
        })
        if error not in target["validator_errors"]:
            target["validator_errors"].append(error)
        if repair not in target["required_repairs"]:
            target["required_repairs"].append(repair)

    factual_repairs = {
        "unsupported_year": "Replace or remove only the unsupported year using the listed approved claims.",
        "unsupported_number": "Replace or remove only the unsupported number using the listed approved claims.",
        "unsupported_name": "Remove the unsupported name or use a name verbatim from the listed approved claims.",
    }
    for violation in report.get("factual_lock_violations", []):
        if isinstance(violation, dict):
            category = str(violation.get("category", ""))
            add(str(violation.get("passage", "")), f"{category}: {violation.get('value')}", factual_repairs.get(category, "Remove the unsupported fact."))

    phrase_specs = [
        (report.get("overdramatic_phrases", []), "AI cliché", "Remove the cliché and end on a concrete fact from the listed approved claims."),
        (report.get("unnatural_phrasing", []), "Unnatural Dutch", "Make this sentence natural spoken Dutch without changing any fact."),
        (report.get("narration_metadata", []), "Narration metadata", "Remove only the metadata token from narration."),
    ]
    for values, error, repair in phrase_specs:
        for value in values:
            for sentence in sentences:
                if str(value).casefold() in sentence.casefold():
                    add(sentence, f"{error}: {value}", repair)

    if report.get("long_sentence_count"):
        for sentence in sentences:
            if len(re.findall(r"\b[\w'-]+\b", sentence)) > 32:
                add(sentence, "Sentence exceeds 32 words", "Split only this sentence into shorter factual sentences.")

    if any("rhetorical questions" in reason.casefold() for reason in report.get("language_rejection_reasons", [])):
        for sentence in sentences:
            if sentence.endswith("?"):
                add(sentence, "Rhetorical question", "Replace this question with a declarative, claim-backed sentence.")

    word_count_repair = _word_count_repair(script, report, claims)
    if word_count_repair:
        add(
            str(word_count_repair["original_passage"]),
            str(word_count_repair["validator_errors"][0]),
            str(word_count_repair["required_repairs"][0]),
            target_id=str(word_count_repair["target_id"]),
        )
        for extra_repair in word_count_repair["required_repairs"][1:]:
            target = targets.get(str(word_count_repair["original_passage"]))
            if target and extra_repair not in target["required_repairs"]:
                target["required_repairs"].append(extra_repair)
        if word_count_repair["approved_claims"]:
            target = targets.get(str(word_count_repair["original_passage"]))
            if target is not None:
                target["approved_claims"] = word_count_repair["approved_claims"]

    if report.get("narration_section_mismatch"):
        section_sentences = {sentence for section in script.get("sections", []) if isinstance(section, dict)
                             for sentence in _sentences(str(section.get("text", "")))}
        for sentence in sentences:
            if sentence not in section_sentences:
                add(sentence, "Narration differs from ordered section text",
                    "Remove this unmatched passage or replace it with the exact corresponding section sentence.")

    repairs = list(targets.values())
    return {
        "version": 2,
        "status": "repair_required" if repairs else "no_surgical_targets",
        "source_failure_reasons": list(report.get("failure_reasons", [])),
        "repairs": repairs,
    }


def apply_surgical_replacements(
    script: dict[str, Any], plan: dict[str, Any], response: dict[str, Any],
    claims: list[dict[str, Any]], language: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Apply an all-or-nothing passage patch after local factual checks."""
    targets = {str(item.get("target_id")): item for item in plan.get("repairs", []) if isinstance(item, dict)}
    replacements = response.get("replacements", []) if isinstance(response, dict) else []
    supplied = {str(item.get("target_id")): item for item in replacements if isinstance(item, dict)}
    errors: list[str] = []
    if set(supplied) != set(targets):
        errors.append("Replacement response must contain exactly one replacement for every repair target.")
        return None, errors
    candidate = deepcopy(script)
    expected_narration = str(script.get("narration", ""))
    for target_id, target in targets.items():
        original = str(target.get("original_passage", ""))
        replacement = str(supplied[target_id].get("replacement_passage", ""))
        if not original or str(candidate.get("narration", "")).count(original) != 1:
            errors.append(f"{target_id}: target passage is not unique in narration.")
            continue
        factual = factual_lock_issues(replacement, claims, language)
        if any(factual.values()):
            errors.append(f"{target_id}: replacement violates factual lock: {factual}.")
            continue
        candidate["narration"] = str(candidate["narration"]).replace(original, replacement, 1)
        expected_narration = expected_narration.replace(original, replacement, 1)
        for section in candidate.get("sections", []):
            if isinstance(section, dict) and original in str(section.get("text", "")):
                if str(section["text"]).count(original) != 1:
                    errors.append(f"{target_id}: target passage is not unique in section text.")
                    break
                section["text"] = str(section["text"]).replace(original, replacement, 1)
    if errors:
        return None, errors
    if str(candidate.get("narration", "")) != expected_narration:
        return None, ["Non-target narration changed during surgical replacement."]
    original_metadata = {key: value for key, value in script.items() if key not in {"narration", "sections"}}
    candidate_metadata = {key: value for key, value in candidate.items() if key not in {"narration", "sections"}}
    if candidate_metadata != original_metadata:
        return None, ["Non-target script metadata changed during surgical replacement."]
    return candidate, []


def run_writer_critic_rewriter(
    project_root: Path, initial_script: dict[str, Any], provider: ReasoningProvider,
    claims: list[dict[str, Any]], architecture: dict[str, Any], script_config: dict[str, Any],
    research_plan: dict[str, Any], dossier: dict[str, Any], narrative_outline: dict[str, Any],
    target_duration_minutes: int, language: str, *, maximum_model_calls: int = MAX_MODEL_CALLS,
    artifact_directory: Path | None = None, promote: Any | None = None,
) -> tuple[dict[str, Any] | None, list[tuple[int, dict[str, Any]]]]:
    if maximum_model_calls not in range(1, MAX_MODEL_CALLS + 1):
        raise ValueError("maximum_model_calls must be between 1 and 3.")
    candidate = initial_script
    attempts: list[tuple[int, dict[str, Any]]] = []
    pending_report: dict[str, Any] | None = None
    for candidate_id in range(1, maximum_model_calls + 1):
        report = pending_report or validate_script(candidate, claims, architecture, script_config)
        pending_report = None
        attempts.append((candidate_id, report))
        if artifact_directory is not None:
            write_json(artifact_directory / f"script_candidate_{candidate_id}.json", candidate)
            write_json(artifact_directory / f"script_candidate_{candidate_id}_quality_report.json", report)
        if report["pass"]:
            if promote is not None:
                promote(candidate_id, candidate, report)
            return candidate, attempts
        if not report.get("failure_reasons") or candidate_id == maximum_model_calls or not provider.available:
            break
        plan = build_script_repair_plan(candidate, report, claims)
        if artifact_directory is not None:
            write_json(artifact_directory / f"script_candidate_{candidate_id}_repair_plan.json", plan)
        if not plan["repairs"]:
            break
        response = provider.rewrite_script_passages(project_root, plan, target_duration_minutes, language)
        repaired, errors = apply_surgical_replacements(candidate, plan, response, claims, language)
        if errors:
            if artifact_directory is not None:
                write_json(artifact_directory / f"script_candidate_{candidate_id}_replacement_rejection.json", {"errors": errors})
            pending_report = report
            continue
        if repaired is not None:
            repaired_report = validate_script(repaired, claims, architecture, script_config)
            previous_issues = _semantic_issue_map(report)
            repaired_issues = _semantic_issue_map(repaired_report)
            new_issue_keys = sorted(set(repaired_issues) - set(previous_issues))
            if new_issue_keys:
                if artifact_directory is not None:
                    write_json(artifact_directory / f"script_candidate_{candidate_id}_replacement_rejection.json", {
                        "errors": ["Replacement introduced new validator failures."],
                        "new_failure_keys": new_issue_keys,
                        "new_failure_reasons": [repaired_issues[key] for key in new_issue_keys],
                    })
                pending_report = report
                continue
            candidate = repaired
            pending_report = repaired_report
    return None, attempts
