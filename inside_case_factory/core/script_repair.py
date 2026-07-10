from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from inside_case_factory.core.narrative_quality import validate_script
from inside_case_factory.providers.reasoning import ReasoningProvider
from inside_case_factory.utils.files import write_json


MAX_MODEL_CALLS = 3


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if part.strip()]


def build_script_repair_plan(script: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Turn validator evidence into a bounded, deterministic repair plan."""
    narration = str(script.get("narration", ""))
    sentences = _sentences(narration)
    issue_specs = [
        ("unsupported_year", report.get("unsupported_narrated_years", []), "Replace with a year explicitly present in approved claims."),
        ("unsupported_number", report.get("unsupported_narrated_numbers", []), "Remove or replace with a number explicitly present in approved claims."),
        ("unsupported_name", report.get("unsupported_narrated_names", []), "Remove the name unless it appears verbatim in approved claims."),
        ("ai_cliche", report.get("overdramatic_phrases", []), "Rewrite concretely without the detected cliché."),
        ("unnatural_dutch", report.get("unnatural_phrasing", []), "Rewrite this passage as natural spoken Dutch."),
        ("metadata", report.get("narration_metadata", []), "Remove metadata from narration; retain it only in structured fields."),
    ]
    issues: list[dict[str, Any]] = []
    factual_passages = {
        (str(item.get("category")), str(item.get("value"))): [str(item.get("passage"))]
        for item in report.get("factual_lock_violations", []) if isinstance(item, dict)
    }
    for category, values, instruction in issue_specs:
        for value in values:
            needle = str(value)
            passages = [sentence for sentence in sentences if needle.casefold() in sentence.casefold()]
            passages = factual_passages.get((category, needle), passages)
            if category in {"unsupported_year", "unsupported_number"}:
                passages = [sentence for sentence in sentences if re.search(rf"\b{re.escape(needle)}\b", sentence)] or passages
            issues.append({"category": category, "value": value, "passages": passages, "reason": instruction, "action": instruction})
    if report.get("long_sentence_count"):
        for sentence in sentences:
            if len(re.findall(r"\b[\w'-]+\b", sentence)) > 32:
                issues.append({"category": "long_sentence", "value": len(sentence.split()), "passages": [sentence],
                               "reason": "Sentence exceeds 32 spoken words.", "action": "Split it without changing facts."})
    if any("rhetorical questions" in reason.casefold() for reason in report.get("language_rejection_reasons", [])):
        for sentence in sentences:
            if sentence.endswith("?"):
                issues.append({"category": "rhetorical_question", "value": sentence, "passages": [sentence],
                               "reason": "Narration may not contain rhetorical questions.", "action": "State only supported information declaratively."})
    if report.get("word_count", 0) < report.get("minimum_words", 0):
        issues.append({"category": "word_count", "value": report.get("word_count"), "passages": [],
                       "reason": "Narration is below the minimum word count.",
                       "action": "Add only omitted details from approved claims and story architecture."})
    if report.get("word_count", 0) > report.get("maximum_words", 10**9):
        issues.append({"category": "word_count", "value": report.get("word_count"), "passages": [],
                       "reason": "Narration exceeds the maximum word count.", "action": "Condense wording without removing required facts."})
    covered = {str(item["reason"]) for item in issues}
    for reason in report.get("failure_reasons", []):
        if reason not in covered and not any(reason.casefold() in str(item).casefold() for item in issues):
            issues.append({"category": "validator_failure", "value": reason, "passages": [], "reason": reason,
                           "action": "Repair only the minimum text necessary to satisfy this validator failure."})
    return {
        "version": 1,
        "status": "repair_required" if issues else "no_repair",
        "source_failure_reasons": list(report.get("failure_reasons", [])),
        "issues": issues,
        "constraints": [
            "Do not introduce facts, names, dates, years, numbers, events, or conclusions.",
            "Preserve every correct passage and every structured claim_id and beat_id.",
            "Change only passages identified by this plan, except minimal adjacent grammar edits.",
        ],
    }


def run_writer_critic_rewriter(
    project_root: Path,
    initial_script: dict[str, Any],
    provider: ReasoningProvider,
    claims: list[dict[str, Any]],
    architecture: dict[str, Any],
    script_config: dict[str, Any],
    research_plan: dict[str, Any],
    dossier: dict[str, Any],
    narrative_outline: dict[str, Any],
    target_duration_minutes: int,
    language: str,
    *,
    maximum_model_calls: int = MAX_MODEL_CALLS,
    artifact_directory: Path | None = None,
    promote: Any | None = None,
) -> tuple[dict[str, Any] | None, list[tuple[int, dict[str, Any]]]]:
    if maximum_model_calls not in range(1, MAX_MODEL_CALLS + 1):
        raise ValueError("maximum_model_calls must be between 1 and 3.")
    candidate = initial_script
    attempts: list[tuple[int, dict[str, Any]]] = []
    for candidate_id in range(1, maximum_model_calls + 1):
        report = validate_script(candidate, claims, architecture, script_config)
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
        plan = build_script_repair_plan(candidate, report)
        if artifact_directory is not None:
            write_json(artifact_directory / f"script_candidate_{candidate_id}_repair_plan.json", plan)
        candidate = provider.rewrite_script(
            project_root, candidate, claims, architecture, plan, research_plan, dossier,
            narrative_outline, target_duration_minutes, language,
            word_range=(int(script_config.get("minimum_words", 1500)), int(script_config.get("maximum_words", 1700))),
        )
    return None, attempts
