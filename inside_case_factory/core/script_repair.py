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


def _claim_context(script: dict[str, Any], passage: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = [section for section in script.get("sections", []) if isinstance(section, dict)]
    matching = [section for section in sections if passage in str(section.get("text", ""))]
    if not matching and sections and passage in str(script.get("narration", "")):
        matching = [sections[-1]]
    ids = {str(item) for section in matching for item in section.get("claim_ids", [])}
    return [{"id": claim.get("id"), "text": claim.get("text"), "date": claim.get("date", "")}
            for claim in claims if str(claim.get("id")) in ids]


def build_script_repair_plan(
    script: dict[str, Any], report: dict[str, Any], claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create exact passage replacements; never emit a broad rewrite instruction."""
    claims = claims or []
    narration = str(script.get("narration", ""))
    sentences = _sentences(narration)
    targets: dict[str, dict[str, Any]] = {}

    def add(passage: str, error: str, repair: str) -> None:
        if not passage or passage not in narration:
            return
        target = targets.setdefault(passage, {
            "target_id": f"repair_{len(targets) + 1:02d}",
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
            new_failures = sorted(set(repaired_report.get("failure_reasons", [])) - set(report.get("failure_reasons", [])))
            if new_failures:
                if artifact_directory is not None:
                    write_json(artifact_directory / f"script_candidate_{candidate_id}_replacement_rejection.json", {
                        "errors": ["Replacement introduced new validator failures."], "new_failure_reasons": new_failures,
                    })
                pending_report = report
                continue
            candidate = repaired
            pending_report = repaired_report
    return None, attempts
