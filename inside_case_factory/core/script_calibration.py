from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.narrative_quality import validate_script, validate_story_architecture
from inside_case_factory.providers.reasoning import OpenAIReasoningProvider, reasoning_config_from_settings
from inside_case_factory.utils.files import read_json, write_json


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "dutch_script_calibration.json"
PROTECTED_ARTIFACTS = {"script.json", "script_quality_report.json", "accepted_script_artifact.json"}
CALIBRATION_LIMITS = {
    "minimum_words": 300, "target_words": 400, "maximum_words": 500,
    "words_per_minute": 125, "duration_tolerance": 1.0,
    "minimum_story_beat_coverage": 1.0, "language": "Nederlands",
}


def script_stage_maximum_cost(settings: Settings) -> float:
    config = reasoning_config_from_settings(settings.providers.get("reasoning", {}))
    stage = config.stage("script")
    return round(
        stage["max_input_tokens"] / 1_000_000 * stage["input_cost_per_million_tokens_usd"]
        + stage["max_output_tokens"] / 1_000_000 * stage["output_cost_per_million_tokens_usd"], 6,
    )


def assert_calibration_isolated(repository_root: Path, output_root: Path) -> None:
    resolved_repo = repository_root.resolve()
    resolved_output = output_root.resolve()
    projects = (resolved_repo / "projects").resolve()
    if resolved_output == projects or projects in resolved_output.parents:
        raise ValueError("Calibration output cannot be inside the production projects directory.")
    if resolved_output.exists():
        raise ValueError(f"Calibration output already exists; refusing to overwrite it: {resolved_output}")
    if any(part in PROTECTED_ARTIFACTS for part in resolved_output.parts):
        raise ValueError("Calibration output path uses a protected production artifact name.")


def run_dutch_script_calibration(settings: Settings, output_root: Path, *, maximum_attempts: int = 1) -> dict[str, Any]:
    if maximum_attempts not in range(1, 4):
        raise ValueError("Calibration maximum_attempts must be between 1 and 3.")
    maximum_cost = script_stage_maximum_cost(settings)
    if maximum_cost > 0.05:
        raise RuntimeError(f"Maximum expected script cost ${maximum_cost:.6f} exceeds $0.05; API call blocked.")
    assert_calibration_isolated(settings.root, output_root)
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    architecture = fixture["story_architecture"]
    architecture_report = validate_story_architecture(architecture)
    if not architecture_report["valid"]:
        raise ValueError("Calibration story architecture is invalid: " + "; ".join(architecture_report["errors"]))

    manifests = output_root / "manifests"
    manifests.mkdir(parents=True)
    write_json(manifests / "story_architecture.json", architecture)
    write_json(manifests / "workflow.json", {"language": "Nederlands", "content_mode": "factual_documentary"})
    config = reasoning_config_from_settings(settings.providers.get("reasoning", {}))
    maximum_total_cost = round(maximum_cost * maximum_attempts, 6)
    if maximum_total_cost > config.per_project_spending_limit_usd:
        raise RuntimeError(
            f"Maximum expected calibration cost ${maximum_total_cost:.6f} exceeds the project budget "
            f"${config.per_project_spending_limit_usd:.6f}; API calls blocked."
        )
    write_json(manifests / "paid_api_confirmation.json", {
        "confirmed": True,
        "scope": f"up to {maximum_attempts} calibration script calls, validator retries only",
        "maximum_attempts": maximum_attempts,
        "maximum_expected_cost_usd": maximum_total_cost,
    })

    provider = OpenAIReasoningProvider(config)
    attempts: list[dict[str, Any]] = []
    quality: dict[str, Any] | None = None
    script: dict[str, Any] | None = None
    for attempt in range(1, maximum_attempts + 1):
        script = provider.write_script(
            output_root, fixture["research_plan"], fixture["dossier"], fixture["narrative_outline"],
            fixture["approved_claims"], 3, "Nederlands", quality_report=quality,
            word_range=(300, 500),
        )
        quality = validate_script(script, fixture["approved_claims"], architecture, CALIBRATION_LIMITS)
        write_json(output_root / f"script_candidate_{attempt}.json", script)
        write_json(output_root / f"script_candidate_{attempt}_quality_report.json", quality)
        attempts.append({
            "attempt": attempt, "overall_acceptance": quality["pass"],
            "word_count": quality["word_count"], "rejection_reasons": quality["failure_reasons"],
        })
        if quality["pass"]:
            break
        if not quality.get("failure_reasons"):
            break
    assert script is not None and quality is not None
    usage_manifest = read_json(manifests / "reasoning_usage.json")
    usage_calls = usage_manifest["calls"]
    usage = usage_calls[-1]
    report = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "model": usage["model"],
        "reasoning_effort": usage["reasoning_effort"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "maximum_expected_api_cost_usd": maximum_total_cost,
        "estimated_api_cost_usd": round(sum(call["token_based_estimated_cost_usd"] for call in usage_calls), 6),
        "attempts_used": len(attempts),
        "attempts": attempts,
        "word_count": quality["word_count"],
        "estimated_narration_duration_minutes": quality["estimated_duration_minutes"],
        "narration": script["narration"],
        "dutch_language_quality": quality["dutch_language_quality"],
        "translated_english_patterns": quality["translated_english_patterns"],
        "unnatural_phrasing": quality["unnatural_phrasing"],
        "repeated_sentence_patterns": quality["repeated_sentence_patterns"],
        "overdramatic_phrases": quality["overdramatic_phrases"],
        "spoken_language_issues": quality["spoken_language_issues"],
        "long_sentence_count": quality["long_sentence_count"],
        "connector_repetition": quality["connector_repetition"],
        "language_rejection_reasons": quality["language_rejection_reasons"],
        "overall_acceptance": quality["pass"],
        "all_acceptance_reasons": quality["failure_reasons"],
        "human_review": {
            "sounds_naturally_dutch": "", "sounds_ai_generated": "",
            "documentary_tone_quality": "", "narration_flow_quality": "",
            "factual_clarity": "", "validator_false_positives": "",
            "validator_missed_problems": "", "reviewer_notes": "",
        },
    }
    write_json(output_root / "calibration_report.json", report)
    if quality["pass"]:
        write_json(output_root / "best_valid_script_candidate.json", script)
    return report
