from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
from typing import Any

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.discovery import DiscoveryQuery, discover_archival_media
from inside_case_factory.core.project import create_project, slugify
from inside_case_factory.core.content_modes import normalize_content_mode
from inside_case_factory.core.research import (
    approved_claims,
    approved_sources,
    approve_research,
    approve_script,
    ensure_research_manifests,
    generate_scenes,
    generate_script,
    load_manifest,
    save_manifest,
    tavily_config_from_settings,
)
from inside_case_factory.core.narrative_quality import validate_architecture_file
from inside_case_factory.core.script_repair import run_writer_critic_rewriter
from inside_case_factory.providers.reasoning import (
    DisabledReasoningProvider,
    OpenAIReasoningProvider,
    ReasoningProvider,
    ReasoningProviderError,
    fallback_research_plan,
    estimate_reasoning_cost,
    paid_api_confirmed,
    reasoning_config_from_settings,
    reasoning_provider_from_settings,
)
from inside_case_factory.utils.files import read_json, write_json


def _persist_candidate(project_root: Path, candidate_id: int, script: dict[str, Any], report: dict[str, Any]) -> None:
    manifests = project_root / "manifests"
    write_json(manifests / f"script_candidate_{candidate_id}.json", script)
    enriched = {**report, "candidate_id": candidate_id}
    write_json(manifests / f"script_candidate_{candidate_id}_quality_report.json", enriched)


def _promote_candidate(project_root: Path, candidate_id: int, script: dict[str, Any], report: dict[str, Any]) -> None:
    manifests = project_root / "manifests"
    accepted_script = {**script, "accepted_candidate_id": candidate_id}
    accepted_report = {**report, "candidate_id": candidate_id, "accepted_candidate_id": candidate_id}
    # Write both temporary files completely, then replace the accepted pair.
    script_tmp = manifests / ".script.json.tmp"
    report_tmp = manifests / ".script_quality_report.json.tmp"
    write_json(script_tmp, accepted_script)
    write_json(report_tmp, accepted_report)
    artifact_tmp = manifests / ".accepted_script_artifact.json.tmp"
    write_json(artifact_tmp, {"version": 1, "accepted_candidate_id": candidate_id, "script": accepted_script, "quality_report": accepted_report})
    artifact_tmp.replace(manifests / "accepted_script_artifact.json")
    script_tmp.replace(manifests / "script.json")
    report_tmp.replace(manifests / "script_quality_report.json")


def _write_generation_failure(project_root: Path, attempts: list[tuple[int, dict[str, Any]]], revision_used: bool) -> None:
    metrics = []
    for candidate_id, report in attempts:
        metrics.append({
            "candidate_id": candidate_id, "word_count": report["word_count"],
            "estimated_duration_minutes": report["estimated_duration_minutes"],
            "represented_beat_ids": report["represented_beat_ids"], "missing_beat_ids": report["missing_beat_ids"],
            "unknown_beat_ids": report["unknown_beat_ids"], "duplicate_beat_ids": report["duplicate_beat_ids"],
            "required_research_details_missing": report["unused_required_research_details"],
            "unsupported_claim_ids": report["unsupported_claim_ids"], "style_violations": report["banned_style_phrases"],
            "opening_failure": report["opening_quality"] != "pass", "ending_failure": report["ending_quality"] != "pass",
            "chronology_failures": report.get("chronology_failures", []), "transition_failures": report["repetitive_transitions"],
            "rejection_reasons": report["failure_reasons"],
        })
    write_json(project_root / "manifests" / "script_generation_failure.json", {
        "version": 1, "candidate_ids": [item[0] for item in attempts], "candidates": metrics,
        "revision_used": revision_used, "final_rejection_reason": "; ".join(attempts[-1][1]["failure_reasons"]),
    })


def _generate_validated_script_candidates(
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
) -> tuple[dict[str, Any] | None, list[tuple[int, dict[str, Any]]]]:
    maximum_attempts = min(3, max(1, 1 + int(script_config.get("maximum_revision_attempts", 2))))
    return run_writer_critic_rewriter(
        project_root, initial_script, provider, claims, architecture, script_config,
        research_plan, dossier, narrative_outline, target_duration_minutes, language,
        maximum_model_calls=maximum_attempts, artifact_directory=project_root / "manifests",
        promote=lambda candidate_id, script, report: _promote_candidate(project_root, candidate_id, script, report),
    )


PRODUCTION_STAGES = [
    "create_project",
    "research_plan",
    "research",
    "analyze_sources",
    "extract_claims",
    "build_dossier",
    "review_sources_claims",
    "approve_research",
    "narrative_outline",
    "generate_script",
    "review_edit_script",
    "approve_script",
    "generate_scenes",
    "discover_media",
    "review_media",
    "generate_voiceover",
    "render_video",
]


@dataclass(frozen=True)
class ProductionRequest:
    prompt: str
    target_duration_minutes: int = 10
    language: str = "English"
    autonomy_mode: str = "review"
    content_mode: str = "factual_documentary"


def infer_topic(prompt: str) -> str:
    match = re.search(r"\babout\s+(.+?)(?:\.|$)", prompt, re.IGNORECASE)
    if match:
        topic = match.group(1)
    else:
        topic = prompt
    topic = re.sub(r"^(the\s+)?", "", topic.strip(), flags=re.IGNORECASE)
    topic = re.split(r"\b(focus on|make it|use real|strictly factual)\b", topic, flags=re.IGNORECASE)[0]
    return topic.strip(" .")[:120] or "Untitled Inside the Case Documentary"


def production_manifest_path(project_root: Path, name: str) -> Path:
    return project_root / "manifests" / name


def append_activity(project_root: Path, message: str, *, stage: str = "") -> None:
    path = production_manifest_path(project_root, "production_activity.json")
    if path.exists():
        data = read_json(path)
    else:
        data = {"version": 1, "current_activity": "", "log": []}
    log = data.setdefault("log", [])
    entry = {"at": datetime.now(UTC).isoformat(), "stage": stage, "message": message}
    if isinstance(log, list):
        log.append(entry)
    data["current_activity"] = message
    data["current_stage"] = stage
    write_json(path, data)


def write_plan(project_root: Path, request: ProductionRequest, topic: str) -> None:
    write_json(
        production_manifest_path(project_root, "production_plan.json"),
        {
            "version": 1,
            "topic": topic,
            "prompt": request.prompt,
            "target_duration_minutes": request.target_duration_minutes,
            "language": request.language,
            "autonomy_mode": request.autonomy_mode,
            "content_mode": normalize_content_mode(request.content_mode),
            "publishing": "disabled",
            "stages": [
                {
                    "id": stage,
                    "status": "pending",
                    "requires_human_review": stage
                    in {"review_sources_claims", "approve_research", "review_edit_script", "approve_script", "review_media"},
                }
                for stage in PRODUCTION_STAGES
            ],
        },
    )


def update_plan_stage(project_root: Path, stage_id: str, status: str, note: str = "") -> None:
    path = production_manifest_path(project_root, "production_plan.json")
    if not path.exists():
        return
    plan = read_json(path)
    stages = plan.get("stages", [])
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and stage.get("id") == stage_id:
                stage["status"] = status
                if note:
                    stage["note"] = note
                stage["updated_at"] = datetime.now(UTC).isoformat()
                break
    write_json(path, plan)


def start_production(settings: Settings, request: ProductionRequest) -> dict[str, Any]:
    topic = infer_topic(request.prompt)
    slug = unique_project_slug(settings.projects_dir, slugify(topic))
    project = create_project(settings.projects_dir, topic, slug)
    ensure_research_manifests(project.root)
    workflow = load_manifest(project.root, "workflow.json")
    workflow["target_duration_minutes"] = request.target_duration_minutes
    workflow["language"] = request.language
    workflow["autonomy_mode"] = request.autonomy_mode
    workflow["content_mode"] = normalize_content_mode(request.content_mode)
    save_manifest(project.root, "workflow.json", workflow)
    write_json(production_manifest_path(project.root, "production_request.json"), request.__dict__ | {"topic": topic})
    write_plan(project.root, request, topic)
    cost_estimate = estimate_reasoning_cost(reasoning_config_from_settings(settings.providers.get("reasoning", {})))
    write_json(production_manifest_path(project.root, "cost_estimate.json"), cost_estimate)
    append_activity(project.root, "Project created from prompt.", stage="create_project")
    update_plan_stage(project.root, "create_project", "completed")

    run_production(settings, project.root)
    return {"project_slug": project.slug, "project_root": str(project.root), "topic": topic}


def run_production(settings: Settings, project_root: Path) -> None:
    workflow = load_manifest(project_root, "workflow.json")
    plan = read_json(production_manifest_path(project_root, "production_plan.json"))
    request = read_json(production_manifest_path(project_root, "production_request.json"))
    autonomy = str(plan.get("autonomy_mode", "review"))
    topic = str(plan.get("topic", project_root.name))
    reasoning_provider = reasoning_provider_from_settings(settings.providers.get("reasoning", {}))

    try:
        research_plan = create_research_plan(project_root, request, topic, reasoning_provider)
        update_plan_stage(project_root, "research_plan", "completed")
    except ReasoningProviderError as error:
        research = load_manifest(project_root, "research.json")
        research.update(
            {
                "provider": "openai",
                "status": "blocked",
                "topic": topic,
                "message": str(error),
                "ran_at": datetime.now(UTC).isoformat(),
            }
        )
        save_manifest(project_root, "research.json", research)
        append_activity(project_root, f"Reasoning paused safely: {error}", stage="research_plan")
        update_plan_stage(project_root, "research", "blocked", str(error))
        return

    append_activity(project_root, "Starting research stage.", stage="research")
    research_result = run_research(settings, project_root, topic, reasoning_provider=reasoning_provider, research_plan=research_plan)
    update_plan_stage(
        project_root,
        "research",
        "completed" if research_result.get("ok") else "blocked",
        str(research_result.get("message", "")),
    )
    if research_result.get("ok"):
        update_plan_stage(project_root, "analyze_sources", "completed" if (project_root / "manifests" / "source_analysis.json").exists() else "pending")
        update_plan_stage(project_root, "extract_claims", "completed" if int(research_result.get("claims_added", 0) or 0) else "waiting_for_review")
        update_plan_stage(project_root, "build_dossier", "completed" if (project_root / "manifests" / "dossier.json").exists() else "pending")
    update_plan_stage(project_root, "review_sources_claims", "waiting_for_review")
    append_activity(project_root, "Research stage complete. Review sources and claims.", stage="review_sources_claims")

    if autonomy == "review":
        append_activity(project_root, "Review Mode pause: approve research before script generation.", stage="approve_research")
        update_plan_stage(project_root, "approve_research", "waiting_for_review")
        return

    if not (approved_sources(project_root) and approved_claims(project_root) and workflow.get("research_approved")):
        append_activity(project_root, "Automatic Mode paused because research approval is still required.", stage="approve_research")
        update_plan_stage(project_root, "approve_research", "waiting_for_review")
        return

    try:
        architecture_path = project_root / "manifests" / "story_architecture.json"
        architecture = read_json(architecture_path) if architecture_path.exists() else {}
        architecture_report = validate_architecture_file(project_root, architecture)
        if not architecture_report["valid"]:
            raise RuntimeError("Malformed story architecture: " + "; ".join(architecture_report["errors"]))
        generated_script = generate_script(project_root, int(request.get("target_duration_minutes", 10)), reasoning_provider=reasoning_provider)
        script_config = {**settings.script, "language": str(request.get("language", workflow.get("language", "English")))}
        claims = approved_claims(project_root)
        generated_script, attempts = _generate_validated_script_candidates(
            project_root, generated_script, reasoning_provider, claims, architecture, script_config,
            read_json(project_root / "manifests" / "research_plan.json"),
            read_json(project_root / "manifests" / "dossier.json"),
            read_json(project_root / "manifests" / "narrative_outline.json"),
            int(request.get("target_duration_minutes", 10)), str(request.get("language", "English")),
        )
        if generated_script is None:
            quality = attempts[-1][1]
            _write_generation_failure(project_root, attempts, len(attempts) > 1)
            update_plan_stage(project_root, "generate_script", "blocked", "; ".join(quality["failure_reasons"]))
            append_activity(project_root, "Script rejected by hard quality requirements.", stage="generate_script")
            return
        update_plan_stage(project_root, "narrative_outline", "completed" if (project_root / "manifests" / "narrative_outline.json").exists() else "pending")
        update_plan_stage(project_root, "generate_script", "completed")
        update_plan_stage(project_root, "review_edit_script", "waiting_for_review")
    except Exception as error:
        update_plan_stage(project_root, "generate_script", "blocked", str(error))
        append_activity(project_root, f"Script generation blocked: {error}", stage="generate_script")
        return

    if not workflow.get("script_approved"):
        append_activity(project_root, "Automatic Mode paused because script approval is required.", stage="approve_script")
        update_plan_stage(project_root, "approve_script", "waiting_for_review")
        return

    try:
        generate_scenes(project_root, reasoning_provider=reasoning_provider)
        update_plan_stage(project_root, "generate_scenes", "completed")
        append_activity(project_root, "Scenes generated.", stage="generate_scenes")
    except Exception as error:
        update_plan_stage(project_root, "generate_scenes", "blocked", str(error))
        append_activity(project_root, f"Scene generation blocked: {error}", stage="generate_scenes")
        return

    if (project_root / "manifests" / "scenes.json").exists():
        discover_archival_media(project_root, DiscoveryQuery(topic=topic, limit_per_source=4))
        update_plan_stage(project_root, "discover_media", "completed")
        update_plan_stage(project_root, "review_media", "waiting_for_review")
        append_activity(project_root, "Media discovery complete. Review media before voice-over or render.", stage="review_media")


def create_research_plan(
    project_root: Path,
    request: dict[str, Any],
    topic: str,
    reasoning_provider: ReasoningProvider,
) -> dict[str, Any]:
    if isinstance(reasoning_provider, DisabledReasoningProvider):
        plan = fallback_research_plan(request | {"topic": topic}, "Reasoning provider is disabled.")
        plan["status"] = "draft"
        write_json(project_root / "manifests" / "research_plan.json", plan)
        return plan
    if isinstance(reasoning_provider, OpenAIReasoningProvider) and reasoning_provider.config.enabled and not reasoning_provider.available:
        if reasoning_provider.config.dry_run:
            plan = fallback_research_plan(request | {"topic": topic}, "OpenAI reasoning dry-run mode is enabled.")
            write_json(project_root / "manifests" / "research_plan.json", plan)
            return plan
        raise ReasoningProviderError("OPENAI_API_KEY is not set.")
    if reasoning_provider.available:
        return reasoning_provider.analyze_request(project_root, request | {"topic": topic})
    plan = fallback_research_plan(request | {"topic": topic}, "Reasoning provider is not available.")
    plan["status"] = "draft"
    write_json(project_root / "manifests" / "research_plan.json", plan)
    return plan


def run_research(
    settings: Settings,
    project_root: Path,
    topic: str,
    *,
    reasoning_provider: ReasoningProvider | None = None,
    research_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not os.environ.get("TAVILY_API_KEY"):
        research = load_manifest(project_root, "research.json")
        research.update(
            {
                "provider": "tavily",
                "status": "blocked",
                "topic": topic,
                "message": "TAVILY_API_KEY is not set. Add manual sources/claims or configure Tavily.",
                "ran_at": datetime.now(UTC).isoformat(),
            }
        )
        save_manifest(project_root, "research.json", research)
        return {"ok": False, "message": research["message"]}
    if "allow_paid_providers" in settings.pipeline and not settings.pipeline.get("allow_paid_providers"):
        return {"ok": False, "message": "Paid research providers are disabled. Use manual sources or dry-run mode."}
    if settings.pipeline.get("require_paid_api_confirmation", False) and not paid_api_confirmed(project_root):
        return {"ok": False, "message": "Paid Tavily research requires explicit project confirmation."}
    research_settings = settings.providers.get("research", {})
    tavily_settings = research_settings.get("tavily", {}) if isinstance(research_settings, dict) else {}
    provider = tavily_config_from_settings(tavily_settings)
    return provider.research(project_root, topic, reasoning_provider=reasoning_provider, research_plan=research_plan)


def unique_project_slug(projects_dir: Path, base_slug: str) -> str:
    slug = base_slug or "inside-the-case-project"
    candidate = slug
    counter = 2
    while (projects_dir / candidate).exists():
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate
