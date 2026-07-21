from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import re
from typing import Any

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.discovery import discover_archival_media, discover_project_scene_media
from inside_case_factory.core.autonomous_direction import DirectorEngine
from inside_case_factory.core.producer import ProducerEngine
from inside_case_factory.core.project import create_project, slugify
from inside_case_factory.core.progress import write_progress_event
from inside_case_factory.core.recycle import create_reference_documentary, prepare_recycle_documentary
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
from inside_case_factory.pipeline.generator import generate_video_project
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
    "story_architecture",
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

RUN_QUALITY_EVIDENCE_GRADE = "evidence_grade"
RUN_QUALITY_SAMPLE_OR_DEMO = "sample_or_demo"
RUN_QUALITY_MODES = {RUN_QUALITY_EVIDENCE_GRADE, RUN_QUALITY_SAMPLE_OR_DEMO}


def normalize_run_quality_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    return mode if mode in RUN_QUALITY_MODES else RUN_QUALITY_SAMPLE_OR_DEMO


@dataclass(frozen=True)
class ProductionRequest:
    prompt: str
    target_duration_minutes: int = 10
    language: str = "English"
    autonomy_mode: str = "review"
    content_mode: str = "factual_documentary"
    run_quality_mode: str = RUN_QUALITY_SAMPLE_OR_DEMO
    workflow_type: str = "create_documentary"
    reference_documentary_url: str = ""
    reference_documentary_path: str = ""


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
    lowered = message.lower()
    event = "failed" if any(word in lowered for word in ("failed", "error", "rejected")) else "blocked" if any(word in lowered for word in ("blocked", "paused", "waiting")) else "completed" if any(word in lowered for word in ("complete", "created", "approved")) else "started"
    write_progress_event(project_root, event, stage or "project", message)


def write_plan(project_root: Path, request: ProductionRequest, topic: str) -> None:
    stages = list(PRODUCTION_STAGES)
    if request.workflow_type == "recycle_documentary":
        stages = ["reference_documentary", "analysis", "verification", *stages]
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
            "workflow_type": request.workflow_type,
            "publishing": "disabled",
            "stages": [
                {
                    "id": stage,
                    "status": "pending",
                    "requires_human_review": stage
                    in {"review_sources_claims", "approve_research", "review_edit_script", "approve_script", "review_media"},
                }
                for stage in stages
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
    workflow["run_quality_mode"] = normalize_run_quality_mode(request.run_quality_mode)
    workflow["workflow_type"] = request.workflow_type
    save_manifest(project.root, "workflow.json", workflow)
    write_json(
        production_manifest_path(project.root, "production_request.json"),
        request.__dict__ | {"topic": topic, "run_quality_mode": normalize_run_quality_mode(request.run_quality_mode)},
    )
    if request.workflow_type == "recycle_documentary":
        local_reference_path = Path(request.reference_documentary_path) if request.reference_documentary_path else None
        if request.reference_documentary_url or local_reference_path is not None:
            create_reference_documentary(
                project.root,
                source_url=request.reference_documentary_url,
                local_path=local_reference_path,
                original_filename=local_reference_path.name if local_reference_path is not None else "",
            )
            prepare_recycle_documentary(project.root)
    write_plan(project.root, request, topic)
    cost_estimate = estimate_reasoning_cost(reasoning_config_from_settings(settings.providers.get("reasoning", {})))
    write_json(production_manifest_path(project.root, "cost_estimate.json"), cost_estimate)
    append_activity(project.root, "Project created from prompt.", stage="create_project")
    update_plan_stage(project.root, "create_project", "completed")

    run_production(settings, project.root)
    return {"project_slug": project.slug, "project_root": str(project.root), "topic": topic}


def _orchestration_state(project_root: Path) -> dict[str, Any]:
    path = production_manifest_path(project_root, "orchestration.json")
    if path.exists():
        return read_json(path)
    return {"version": 1, "status": "idle", "current_stage": "", "completed_stages": [], "run_count": 0}


def _save_orchestration(project_root: Path, state: dict[str, Any], **updates: Any) -> None:
    state.update(updates)
    state["updated_at"] = datetime.now(UTC).isoformat()
    write_json(production_manifest_path(project_root, "orchestration.json"), state)


def _complete_stage(project_root: Path, state: dict[str, Any], stage: str) -> None:
    completed = state.setdefault("completed_stages", [])
    if stage not in completed:
        completed.append(stage)
    _save_orchestration(project_root, state, status="running", current_stage=stage, waiting_for="", last_error="")


def _wait_for_approval(project_root: Path, state: dict[str, Any], gate: str) -> None:
    _save_orchestration(project_root, state, status="waiting_for_approval", current_stage=gate, waiting_for=gate)


def _gate_result(
    *,
    stage: str,
    passed: bool,
    run_quality_mode: str,
    blocking_code: str = "",
    blocking_reason: str = "",
    missing_requirements: list[str] | None = None,
    next_action: str = "",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "passed": bool(passed),
        "blocking_code": blocking_code,
        "blocking_reason": blocking_reason,
        "missing_requirements": list(missing_requirements or []),
        "run_quality_mode": run_quality_mode,
        "next_action": next_action,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }


def _persist_gate_result(project_root: Path, gate: dict[str, Any]) -> None:
    path = production_manifest_path(project_root, "quality_cycle.json")
    existing = read_json(path) if path.exists() else {"version": 1, "attempts": []}
    existing.setdefault("attempts", [])
    gates = existing.setdefault("foundation_gates", [])
    if isinstance(gates, list):
        gates.append(gate)
    existing["latest_foundation_gate"] = gate
    write_json(path, existing)


def _set_run_outcome(workflow: dict[str, Any], status: str, run_quality_mode: str) -> None:
    workflow["run_quality_mode"] = run_quality_mode
    workflow["run_outcome_status"] = status
    workflow["is_evidence_grade"] = run_quality_mode == RUN_QUALITY_EVIDENCE_GRADE


def _block_for_gate(
    project_root: Path,
    state: dict[str, Any],
    *,
    stage: str,
    gate: dict[str, Any],
    orchestration_status: str,
    plan_stage: str,
) -> None:
    _persist_gate_result(project_root, gate)
    update_plan_stage(project_root, plan_stage, "blocked", gate.get("blocking_reason", ""))
    append_activity(project_root, gate.get("blocking_reason", "Blocking gate failed."), stage=stage)
    _save_orchestration(
        project_root,
        state,
        status=orchestration_status,
        current_stage=stage,
        last_error=gate.get("blocking_reason", ""),
        latest_foundation_gate=gate,
    )


def run_production(settings: Settings, project_root: Path) -> None:
    """Resume a production idempotently until the next approval gate or completion."""
    lock_path = production_manifest_path(project_root, ".orchestration.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _run_production_locked(settings, project_root)


def recover_invalid_schema_task(project_root: Path) -> bool:
    """Queue a schema-rejected research plan for restart without touching approval."""
    state_path = production_manifest_path(project_root, "orchestration.json")
    if not state_path.exists():
        return False
    state = read_json(state_path)
    error = str(state.get("last_error", ""))
    if state.get("current_stage") != "research_plan" or "invalid_json_schema" not in error:
        return False
    state.update({"status": "queued", "last_error": "", "waiting_for": "", "resume_after_restart": True, "updated_at": datetime.now(UTC).isoformat()})
    write_json(state_path, state)
    update_plan_stage(project_root, "research_plan", "pending", "Schema hersteld; hervat automatisch na dashboardherstart.")
    research = load_manifest(project_root, "research.json")
    research.update({"status": "queued", "message": "Schema hersteld; taak staat veilig klaar om te hervatten."})
    save_manifest(project_root, "research.json", research)
    write_progress_event(project_root, "retrying", "research_plan", "Researchtaak hervat automatisch na dashboardherstart; bestaande kostengoedkeuring behouden")
    return True


def _run_production_locked(settings: Settings, project_root: Path) -> None:
    workflow = load_manifest(project_root, "workflow.json")
    plan = read_json(production_manifest_path(project_root, "production_plan.json"))
    request = read_json(production_manifest_path(project_root, "production_request.json"))
    run_quality_mode = normalize_run_quality_mode(
        request.get("run_quality_mode")
        or workflow.get("run_quality_mode")
        or settings.pipeline.get("run_quality_mode")
    )
    workflow["run_quality_mode"] = run_quality_mode
    save_manifest(project_root, "workflow.json", workflow)
    topic = str(plan.get("topic", project_root.name))
    reasoning_provider = reasoning_provider_from_settings(settings.providers.get("reasoning", {}))
    state = _orchestration_state(project_root)
    state["run_count"] = int(state.get("run_count", 0)) + 1
    _save_orchestration(project_root, state, status="running", waiting_for="", last_error="")

    if workflow.get("workflow_type") == "recycle_documentary" and not workflow.get("recycle_analysis_ready"):
        try:
            append_activity(project_root, "Preparing reference documentary blueprint.", stage="analysis")
            update_plan_stage(project_root, "reference_documentary", "completed")
            prepare_recycle_documentary(project_root)
            workflow = load_manifest(project_root, "workflow.json")
            update_plan_stage(project_root, "analysis", "completed")
            update_plan_stage(project_root, "verification", "completed")
            _complete_stage(project_root, state, "analysis")
        except RuntimeError as error:
            append_activity(project_root, f"Recycle engine blocked: {error}", stage="analysis")
            _save_orchestration(project_root, state, status="blocked", current_stage="analysis", last_error=str(error))
            update_plan_stage(project_root, "analysis", "blocked", str(error))
            return

    research_plan_path = project_root / "manifests" / "research_plan.json"
    if not research_plan_path.exists():
        try:
            research_plan = create_research_plan(project_root, request, topic, reasoning_provider)
            update_plan_stage(project_root, "research_plan", "completed")
            _complete_stage(project_root, state, "research_plan")
        except ReasoningProviderError as error:
            research = load_manifest(project_root, "research.json")
            research.update({
                "provider": "openai", "status": "blocked", "topic": topic,
                "message": str(error), "ran_at": datetime.now(UTC).isoformat(),
            })
            save_manifest(project_root, "research.json", research)
            append_activity(project_root, f"Reasoning paused safely: {error}", stage="research_plan")
            _save_orchestration(project_root, state, status="blocked", current_stage="research_plan", last_error=str(error))
            update_plan_stage(project_root, "research_plan", "blocked", str(error))
            return
    else:
        research_plan = read_json(research_plan_path)
        _complete_stage(project_root, state, "research_plan")

    if not workflow.get("research_approved"):
        if "research" not in state.get("completed_stages", []):
            sources_ready = bool(load_manifest(project_root, "sources.json").get("sources"))
            claims_ready = bool(load_manifest(project_root, "claims.json").get("claims"))
            if sources_ready and claims_ready:
                _complete_stage(project_root, state, "research")
            else:
                _save_orchestration(project_root, state, status="running", current_stage="research")
                result = run_research(settings, project_root, topic, reasoning_provider=reasoning_provider, research_plan=research_plan)
                update_plan_stage(project_root, "research", "completed" if result.get("ok") else "blocked", str(result.get("message", "")))
                if result.get("ok"):
                    _complete_stage(project_root, state, "research")
                else:
                    append_activity(
                        project_root,
                        f"Review Mode pause: research is blocked until prerequisites are available. {result.get('message', '')}",
                        stage="research",
                    )
                    _save_orchestration(project_root, state, status="blocked", current_stage="research", last_error=str(result.get("message", "")))
                    return
        if run_quality_mode == RUN_QUALITY_EVIDENCE_GRADE:
            gate = _gate_result(
                stage="research",
                passed=False,
                run_quality_mode=run_quality_mode,
                blocking_code="missing_research_approval",
                blocking_reason="Evidence-grade run blocked: research approval is required.",
                missing_requirements=["Approved research package"],
                next_action="Approve research with at least one usable source and linked approved claim.",
            )
            workflow = load_manifest(project_root, "workflow.json")
            _set_run_outcome(workflow, "blocked_missing_research", run_quality_mode)
            save_manifest(project_root, "workflow.json", workflow)
            _block_for_gate(
                project_root,
                state,
                stage="research",
                gate=gate,
                orchestration_status="blocked_missing_research",
                plan_stage="approve_research",
            )
            return
        append_activity(project_root, "Research complete. Waiting for definitive research approval.", stage="approve_research")
        update_plan_stage(project_root, "approve_research", "waiting_for_review")
        _wait_for_approval(project_root, state, "research_approval")
        return
    _complete_stage(project_root, state, "research_approval")

    if run_quality_mode == RUN_QUALITY_EVIDENCE_GRADE:
        research = load_manifest(project_root, "research.json")
        approved_source_rows = approved_sources(project_root)
        usable_sources = [
            source for source in approved_source_rows
            if isinstance(source, dict)
            and str(source.get("relevance_status", "relevant")) != "irrelevant"
            and bool(source.get("url") or source.get("publisher") or source.get("title"))
        ]
        research_missing: list[str] = []
        if str(research.get("status", "")).casefold() not in {"completed", "approved"}:
            research_missing.append("Research status must be completed")
        if not usable_sources:
            research_missing.append("At least one usable approved source")
        if research_missing:
            gate = _gate_result(
                stage="research",
                passed=False,
                run_quality_mode=run_quality_mode,
                blocking_code="missing_research_foundation",
                blocking_reason="Evidence-grade run blocked: research foundation is incomplete.",
                missing_requirements=research_missing,
                next_action="Complete research and approve at least one relevant usable source.",
            )
            workflow = load_manifest(project_root, "workflow.json")
            _set_run_outcome(workflow, "blocked_missing_research", run_quality_mode)
            save_manifest(project_root, "workflow.json", workflow)
            _block_for_gate(
                project_root,
                state,
                stage="research",
                gate=gate,
                orchestration_status="blocked_missing_research",
                plan_stage="research",
            )
            return

        usable_source_ids = {str(source.get("id")) for source in usable_sources if str(source.get("id", "")).strip()}
        linked_claims = [
            claim for claim in approved_claims(project_root)
            if isinstance(claim, dict) and any(str(source_id) in usable_source_ids for source_id in claim.get("source_ids", []))
        ]
        if not linked_claims:
            gate = _gate_result(
                stage="claims",
                passed=False,
                run_quality_mode=run_quality_mode,
                blocking_code="missing_approved_claims",
                blocking_reason="Evidence-grade run blocked: no approved source-linked claims were found.",
                missing_requirements=["At least one approved claim linked to usable approved sources"],
                next_action="Approve supported claims that reference approved relevant sources.",
            )
            workflow = load_manifest(project_root, "workflow.json")
            _set_run_outcome(workflow, "blocked_missing_claims", run_quality_mode)
            save_manifest(project_root, "workflow.json", workflow)
            _block_for_gate(
                project_root,
                state,
                stage="claims",
                gate=gate,
                orchestration_status="blocked_missing_claims",
                plan_stage="generate_script",
            )
            return

    script_path = project_root / "manifests" / "script.json"
    if not script_path.exists() or not read_json(script_path).get("narration"):
        try:
            from inside_case_factory.core.narrative_quality import script_word_targets
            duration_minutes = int(request.get("target_duration_minutes", 10))
            word_contract = script_word_targets(
                duration_minutes,
                float(settings.script.get("words_per_minute", 125)),
                float(settings.script.get("duration_tolerance", 1.0)),
            )
            generated_script = generate_script(
                project_root, duration_minutes, reasoning_provider=reasoning_provider,
                word_range=(word_contract["minimum_words"], word_contract["maximum_words"]),
            )
            architecture = read_json(project_root / "manifests" / "story_architecture.json")
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
                _save_orchestration(project_root, state, status="blocked", current_stage="generate_script", last_error="; ".join(quality["failure_reasons"]))
                return
            update_plan_stage(project_root, "story_architecture", "completed")
            update_plan_stage(project_root, "narrative_outline", "completed" if (project_root / "manifests" / "narrative_outline.json").exists() else "pending")
            update_plan_stage(project_root, "generate_script", "completed")
            update_plan_stage(project_root, "review_edit_script", "waiting_for_review")
            _complete_stage(project_root, state, "generate_script")
        except Exception as error:
            update_plan_stage(project_root, "generate_script", "blocked", str(error))
            append_activity(project_root, f"Script generation blocked: {error}", stage="generate_script")
            _save_orchestration(project_root, state, status="blocked", current_stage="generate_script", last_error=str(error))
            return

    workflow = load_manifest(project_root, "workflow.json")
    if not workflow.get("script_approved"):
        append_activity(project_root, "Waiting for definitive script approval.", stage="approve_script")
        update_plan_stage(project_root, "approve_script", "waiting_for_review")
        _wait_for_approval(project_root, state, "script_approval")
        return
    _complete_stage(project_root, state, "script_approval")

    scenes_path = project_root / "manifests" / "scenes.json"
    existing_scenes = read_json(scenes_path).get("scenes", []) if scenes_path.exists() else []
    if not existing_scenes:
        try:
            generate_scenes(project_root, reasoning_provider=reasoning_provider)
            update_plan_stage(project_root, "generate_scenes", "completed")
            _complete_stage(project_root, state, "generate_scenes")
        except Exception as error:
            _save_orchestration(project_root, state, status="blocked", current_stage="generate_scenes", last_error=str(error))
            return
    else:
        _complete_stage(project_root, state, "generate_scenes")

    scenes = read_json(scenes_path).get("scenes", [])
    if not (project_root / "manifests" / "producer_blueprint.json").exists():
        ProducerEngine().plan(project_root, scenes)
        update_plan_stage(project_root, "producer", "completed")

    if "discover_media" not in state.get("completed_stages", []):
        _save_orchestration(project_root, state, status="running", current_stage="discover_media")
        try:
            discover_project_scene_media(project_root, limit_per_source=3)
            update_plan_stage(project_root, "discover_media", "completed")
            _complete_stage(project_root, state, "discover_media")
        except Exception as error:
            update_plan_stage(project_root, "discover_media", "blocked", str(error))
            _save_orchestration(project_root, state, status="blocked", current_stage="discover_media", last_error=str(error))
            return

    media = read_json(project_root / "manifests" / "media_sources.json")
    assets = media.get("assets", []) if isinstance(media, dict) else []
    eligible_assets = [item for item in assets if isinstance(item, dict) and bool(item.get("review_eligible"))]
    if run_quality_mode == RUN_QUALITY_EVIDENCE_GRADE and not eligible_assets:
        gate = _gate_result(
            stage="media",
            passed=False,
            run_quality_mode=run_quality_mode,
            blocking_code="missing_eligible_media",
            blocking_reason="Evidence-grade run blocked: no eligible media assets are available.",
            missing_requirements=["At least one eligible media asset before director finalization"],
            next_action="Discover and approve real media assets that pass relevance and eligibility checks.",
        )
        workflow = load_manifest(project_root, "workflow.json")
        _set_run_outcome(workflow, "blocked_missing_media", run_quality_mode)
        save_manifest(project_root, "workflow.json", workflow)
        _block_for_gate(
            project_root,
            state,
            stage="media",
            gate=gate,
            orchestration_status="blocked_missing_media",
            plan_stage="discover_media",
        )
        return

    if not (project_root / "manifests" / "director_plan.json").exists():
        DirectorEngine().plan(project_root, scenes, width=1920, height=1080)
        update_plan_stage(project_root, "director", "completed")

    statuses = [str(asset.get("review_status", "pending_review")) for asset in assets if isinstance(asset, dict)]
    if run_quality_mode == RUN_QUALITY_SAMPLE_OR_DEMO and not statuses:
        append_activity(project_root, "Demo mode: media review skipped because no candidate assets were discovered.", stage="review_media")
        _complete_stage(project_root, state, "media_approval")
    elif not statuses or "pending_review" in statuses or "approved" not in statuses:
        update_plan_stage(project_root, "review_media", "waiting_for_review")
        append_activity(project_root, "Media discovery complete. Waiting for definitive media review.", stage="review_media")
        _wait_for_approval(project_root, state, "media_approval")
        return
    else:
        _complete_stage(project_root, state, "media_approval")

    final_video = project_root / "exports" / "final_video.mp4"
    if not final_video.exists():
        try:
            generate_video_project(settings, topic, existing_project_root=project_root)
        except Exception as error:
            _save_orchestration(project_root, state, status="interrupted", current_stage="render_video", last_error=str(error))
            raise
    update_plan_stage(project_root, "generate_voiceover", "completed")
    update_plan_stage(project_root, "render_video", "completed")
    _complete_stage(project_root, state, "render_video")
    completion_status = "evidence_grade_completed" if run_quality_mode == RUN_QUALITY_EVIDENCE_GRADE else "demo_completed"
    _save_orchestration(project_root, state, status=completion_status, current_stage="completed", waiting_for="", last_error="")
    workflow = load_manifest(project_root, "workflow.json")
    _set_run_outcome(workflow, completion_status, run_quality_mode)
    save_manifest(project_root, "workflow.json", workflow)
    append_activity(project_root, "Production completed end to end.", stage="render_video")


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
    if settings.pipeline.get("require_paid_api_confirmation", False) and not paid_api_confirmed(project_root, "tavily_research"):
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
