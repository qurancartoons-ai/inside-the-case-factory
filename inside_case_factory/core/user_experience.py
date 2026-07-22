from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import logging
from pathlib import Path
import re
from typing import Any

from inside_case_factory.core.draft_review import create_review_draft
from inside_case_factory.core.progress import TaskQueue
from inside_case_factory.utils.files import read_json, write_json


LOGGER = logging.getLogger(__name__)

CANONICAL_WORKFLOW: tuple[tuple[str, str], ...] = (
    ("topic", "Onderwerp"),
    ("research", "Onderzoek"),
    ("fact_check", "Feitencontrole"),
    ("script", "Script"),
    ("storyboard", "Storyboard"),
    ("media", "Beelden"),
    ("montage", "Montage"),
    ("render", "Render"),
    ("final_review", "Eindcontrole"),
    ("completed", "Voltooid"),
)

# Legacy public constant retained for compatibility with existing tests and callers.
PHASES: tuple[str, ...] = tuple(title for _, title in CANONICAL_WORKFLOW)

_STAGE_INDEX = {stage_id: index for index, (stage_id, _) in enumerate(CANONICAL_WORKFLOW)}

ORCHESTRATION_TO_CANONICAL = {
    "create_project": "topic",
    "reference_documentary": "topic",
    "analysis": "research",
    "verification": "fact_check",
    "research_plan": "research",
    "research": "research",
    "analyze_sources": "research",
    "build_dossier": "research",
    "review_sources_claims": "fact_check",
    "extract_claims": "fact_check",
    "approve_research": "fact_check",
    "research_approval": "fact_check",
    "story_architecture": "script",
    "narrative_outline": "script",
    "generate_script": "script",
    "review_edit_script": "script",
    "approve_script": "script",
    "script_approval": "script",
    "generate_scenes": "storyboard",
    "discover_media": "media",
    "review_media": "media",
    "media_approval": "media",
    "generate_voiceover": "montage",
    "render_video": "render",
    "completed": "completed",
}


def _read_manifest(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return read_json(path) if path.exists() else default


def _as_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _canonical_stage(stage_name: str) -> str:
    lowered = stage_name.strip().lower()
    return ORCHESTRATION_TO_CANONICAL.get(lowered, "")


def _human_stage(stage_id: str) -> str:
    for candidate_id, title in CANONICAL_WORKFLOW:
        if candidate_id == stage_id:
            return title
    return "Onderwerp"


def _has_real_progress_data(orchestration: dict[str, Any], events: list[dict[str, Any]], completed_count: int) -> bool:
    return bool(
        completed_count
        or events
        or orchestration.get("current_stage")
        or orchestration.get("waiting_for")
        or str(orchestration.get("status", "")).strip().lower() not in {"", "idle"}
        or orchestration.get("run_count")
    )


def _activity_text(event: dict[str, Any]) -> str:
    event_type = str(event.get("event", "")).strip().lower()
    stage_id = _canonical_stage(str(event.get("stage", "")))
    stage_title = _human_stage(stage_id)
    message = str(event.get("message", "")).strip()

    if event_type == "source_found" and isinstance(event.get("total"), int):
        return f"{int(event['total'])} bronnen gevonden"
    if event_type == "source_processed":
        if match := re.search(r"(\d+)\s+van\s+(\d+)", message):
            return f"{match.group(1)} bronnen verwerkt"
        return "Bron verwerkt"
    if event_type == "claim_created":
        if isinstance(event.get("total"), int):
            return f"{int(event['total'])} claims opgesteld"
        return "Claim opgesteld"
    if event_type == "started":
        if stage_id == "research":
            return "Onderzoek gestart"
        if stage_id == "render":
            return "Render gestart"
        return f"{stage_title} gestart"
    if event_type == "completed":
        if stage_id == "fact_check":
            return "Feitencontrole voltooid"
        if stage_id == "script":
            return "Scriptconcept aangemaakt"
        if stage_id == "media" and "kandidat" in message.lower():
            return message
        if stage_id == "render":
            return "Eindvideo voltooid"
        if stage_id == "completed":
            return "Eindvideo voltooid"
        return f"{stage_title} voltooid"
    if event_type in {"blocked", "failed"}:
        return message or f"{stage_title} geblokkeerd"
    return message


def _append_metric(metrics: list[dict[str, Any]], label: str, value: Any, *, present: bool) -> None:
    if not present:
        return
    metrics.append({"label": label, "value": value})


def _event_detail(events: Iterable[dict[str, Any]], *keys: str) -> Any:
    for entry in reversed(list(events)):
        for key in keys:
            if key in entry and entry[key] not in {None, ""}:
                return entry[key]
    return None


def _monitor_message(active_stage_id: str, metrics: list[dict[str, Any]]) -> str:
    metric_map = {str(item.get("label", "")): item.get("value") for item in metrics}
    if active_stage_id == "research":
        found = metric_map.get("Bronnen gevonden")
        if found is not None:
            return f"Zoekt {found} bronnen..."
        return "Zoekt bronnen..."
    if active_stage_id == "fact_check":
        return "Controleert historische claims..."
    if active_stage_id == "script":
        return "Schrijft documentairestructuur..."
    if active_stage_id == "storyboard":
        return "Plant scènes en shotvolgorde..."
    if active_stage_id == "media":
        return "Zoekt archiefbeelden en interviews..."
    if active_stage_id == "montage":
        return "Synchroniseert tempo, cuts en ondertitels..."
    if active_stage_id == "render":
        return "Rendert eindvideo..."
    if active_stage_id == "final_review":
        return "Voert eindcontrole uit..."
    if active_stage_id == "completed":
        return "Eindvideo voltooid."
    return "Voorbereiden van productie..."


def _normalize_state(
    *,
    raw_completed: set[str],
    orchestration: dict[str, Any],
) -> tuple[list[dict[str, str]], set[str], bool]:
    inconsistencies: list[str] = []
    contiguous_completed: set[str] = set()
    first_gap: int | None = None
    for index, (stage_id, _) in enumerate(CANONICAL_WORKFLOW):
        if stage_id in raw_completed and first_gap is None:
            contiguous_completed.add(stage_id)
            continue
        if stage_id in raw_completed and first_gap is not None:
            inconsistencies.append(f"late-complete:{stage_id}")
            continue
        if first_gap is None:
            first_gap = index

    current_stage_id = _canonical_stage(str(orchestration.get("current_stage", "")))
    waiting_stage_id = _canonical_stage(str(orchestration.get("waiting_for", "")))
    status_value = str(orchestration.get("status", "")).strip().lower()
    if first_gap is None:
        return [
            {"id": stage_id, "name": title, "status": "Klaar"}
            for stage_id, title in CANONICAL_WORKFLOW
        ], contiguous_completed, bool(inconsistencies)

    active_index = first_gap
    if waiting_stage_id:
        target = _STAGE_INDEX.get(waiting_stage_id, first_gap)
        if target != first_gap:
            inconsistencies.append(f"waiting-stage-mismatch:{waiting_stage_id}")
        active_index = first_gap
    elif current_stage_id:
        target = _STAGE_INDEX.get(current_stage_id, first_gap)
        if target != first_gap:
            inconsistencies.append(f"current-stage-mismatch:{current_stage_id}")
        active_index = first_gap

    active_status = "Bezig"
    if status_value in {"approval_required", "waiting_for_approval"} or waiting_stage_id:
        active_status = "Wacht op jou"
    if status_value in {"failed", "error"}:
        active_status = "Mislukt"
    elif status_value == "blocked":
        active_status = "Geblokkeerd"

    derived: list[dict[str, str]] = []
    for index, (stage_id, title) in enumerate(CANONICAL_WORKFLOW):
        if index < active_index:
            status = "Klaar"
        elif index == active_index:
            status = active_status
        else:
            status = "Niet gestart"
        derived.append({"id": stage_id, "name": title, "status": status})

    repaired = bool(inconsistencies)
    if repaired:
        LOGGER.warning(
            "Normalized inconsistent workflow state for project: status=%s current=%s completed=%s",
            orchestration.get("status"),
            orchestration.get("current_stage"),
            sorted(raw_completed),
        )
    return derived, contiguous_completed, repaired


def production_progress(project_root: Path) -> dict[str, Any]:
    manifests = project_root / "manifests"
    workflow = _read_manifest(manifests / "workflow.json", {})
    orchestration = _read_manifest(manifests / "orchestration.json", {"status": "idle", "current_stage": "", "completed_stages": [], "waiting_for": "", "run_count": 0})
    events_data = _read_manifest(manifests / "progress_events.json", {"events": []})
    events = _as_items(events_data.get("events", []))
    recent_events = events[-25:]

    source_manifest_exists = (manifests / "sources.json").exists()
    claim_manifest_exists = (manifests / "claims.json").exists()
    script_manifest_exists = (manifests / "script.json").exists()
    media_manifest_exists = (manifests / "media_sources.json").exists()
    scenes_manifest_exists = (manifests / "scenes.json").exists()
    timeline_manifest_exists = (manifests / "timeline.json").exists()
    review_manifest_exists = (manifests / "review_draft.json").exists()
    youtube_manifest_exists = (manifests / "youtube_draft.json").exists()

    sources_payload = _read_manifest(manifests / "sources.json", {"sources": []})
    claims_payload = _read_manifest(manifests / "claims.json", {"claims": []})
    script_payload = _read_manifest(manifests / "script.json", {})
    media_payload = _read_manifest(manifests / "media_sources.json", {"assets": []})
    scenes_payload = _read_manifest(manifests / "scenes.json", {"scenes": []})
    timeline_payload = _read_manifest(manifests / "timeline.json", {"scenes": []})
    review_payload = _read_manifest(manifests / "review_draft.json", {"scenes": []})
    youtube_payload = _read_manifest(manifests / "youtube_draft.json", {})
    visual_style = _read_manifest(manifests / "visual_style_profile.json", {})

    sources = _as_items(sources_payload.get("sources", []))
    claims = _as_items(claims_payload.get("claims", []))
    scenes = _as_items(scenes_payload.get("scenes", []))
    timeline_scenes = _as_items(timeline_payload.get("scenes", []))
    review_scenes = _as_items(review_payload.get("scenes", []))
    media_items = _as_items(media_payload.get("assets", []))
    if not media_items:
        media_items = _as_items(media_payload.get("items", []))

    final_video = project_root / "exports" / "final_video.mp4"
    editor_workspace_manifest = _read_manifest(manifests / "editor_workspace.json", {})

    completed_stages = {
        _canonical_stage(str(stage))
        for stage in orchestration.get("completed_stages", [])
        if _canonical_stage(str(stage))
    }
    completed_stages_from_events = {
        _canonical_stage(str(item.get("stage", "")))
        for item in recent_events
        if str(item.get("event", "")).lower() == "completed" and _canonical_stage(str(item.get("stage", "")))
    }

    raw_completed = set(completed_stages) | set(completed_stages_from_events)
    if (project_root / "manifests" / "project.json").exists():
        raw_completed.add("topic")
    if bool(workflow.get("research_approved")):
        raw_completed.update({"research", "fact_check"})
    if bool(workflow.get("script_approved")) and script_manifest_exists:
        raw_completed.add("script")
    if bool(workflow.get("scenes_generated")) and scenes_manifest_exists:
        raw_completed.add("storyboard")
    if any(str(item.get("review_status", "")).lower() in {"approved", "selected"} for item in media_items):
        raw_completed.add("media")
    if timeline_scenes:
        raw_completed.add("montage")
    if final_video.exists() or "render" in completed_stages:
        raw_completed.add("render")
    if review_scenes and all(str(scene.get("review_status", "")).lower() == "approved" for scene in review_scenes):
        raw_completed.add("final_review")
    if bool(youtube_payload.get("upload_confirmed")):
        raw_completed.add("final_review")
    if str(orchestration.get("status", "")).lower() in {"completed", "demo_completed"} and final_video.exists():
        raw_completed.add("completed")

    normalized_stages, normalized_completed, repaired = _normalize_state(raw_completed=raw_completed, orchestration=orchestration)

    completed_count = sum(stage["status"] == "Klaar" for stage in normalized_stages)
    has_real_data = _has_real_progress_data(orchestration, recent_events, completed_count)
    percentage = completed_count * 10 if has_real_data else None

    active_stage = next((stage for stage in normalized_stages if stage["status"] in {"Bezig", "Wacht op jou", "Geblokkeerd", "Mislukt"}), None)
    current_phase = active_stage["name"] if active_stage else "Voltooid" if completed_count == len(CANONICAL_WORKFLOW) else "Onderwerp"

    research_sources_processed = sum(bool(item.get("summary") or item.get("review_status")) for item in sources) if source_manifest_exists else None
    claims_unverified = (
        sum(str(item.get("review_status", "pending_review")).lower() in {"", "pending_review", "needs_review", "draft"} for item in claims)
        if claim_manifest_exists
        else None
    )

    active_metrics: list[dict[str, Any]] = []
    active_stage_id = next((stage["id"] for stage in normalized_stages if stage["status"] in {"Bezig", "Wacht op jou", "Geblokkeerd", "Mislukt"}), "")
    if active_stage_id == "research":
        _append_metric(active_metrics, "Bronnen gevonden", len(sources), present=source_manifest_exists)
        _append_metric(active_metrics, "Bronnen verwerkt", research_sources_processed, present=source_manifest_exists and research_sources_processed is not None)
        _append_metric(active_metrics, "Claims opgesteld", len(claims), present=claim_manifest_exists)
        _append_metric(active_metrics, "Claims wachten op verificatie", claims_unverified, present=claim_manifest_exists and claims_unverified is not None)
    elif active_stage_id == "fact_check":
        if claim_manifest_exists:
            statuses = [str(item.get("review_status", "")).lower() for item in claims]
            checked = sum(state not in {"", "pending_review", "needs_review", "draft"} for state in statuses)
            _append_metric(active_metrics, "Claims gecontroleerd", checked, present=True)
            _append_metric(active_metrics, "Bevestigd", sum(state in {"approved", "confirmed"} for state in statuses), present=True)
            _append_metric(active_metrics, "Betwist", sum(state == "disputed" for state in statuses), present=any(state == "disputed" for state in statuses))
            _append_metric(active_metrics, "Onvoldoende onderbouwd", sum(state in {"unsupported", "rejected"} for state in statuses), present=any(state in {"unsupported", "rejected"} for state in statuses))
    elif active_stage_id == "script":
        if script_manifest_exists:
            narration = str(script_payload.get("narration", ""))
            section_count = len(_as_items(script_payload.get("sections", [])))
            _append_metric(active_metrics, "Secties gegenereerd", section_count, present="sections" in script_payload)
            _append_metric(active_metrics, "Woorden", len([word for word in narration.split() if word]), present=bool(narration))
        _append_metric(active_metrics, "Goedkeuringsstatus", "Goedgekeurd" if workflow.get("script_approved") else "Wacht op goedkeuring", present="script_approved" in workflow)
    elif active_stage_id == "media":
        if media_manifest_exists:
            media_type = [str(item.get("type") or item.get("media_type") or item.get("kind") or "").lower() for item in media_items]
            provider_values = [str(item.get("provider") or item.get("source") or "").lower() for item in media_items]
            selected = sum(str(item.get("review_status", "")).lower() in {"approved", "selected"} for item in media_items)
            rejected = sum(str(item.get("review_status", "")).lower() == "rejected" for item in media_items)
            _append_metric(active_metrics, "Kandidaten gevonden", len(media_items), present=True)
            _append_metric(active_metrics, "Video's", sum("video" in kind for kind in media_type), present=any("video" in kind for kind in media_type))
            _append_metric(active_metrics, "Afbeeldingen", sum("image" in kind or "photo" in kind for kind in media_type), present=any("image" in kind or "photo" in kind for kind in media_type))
            _append_metric(active_metrics, "Archiefassets", sum("archive" in provider or "wikimedia" in provider for provider in provider_values), present=any("archive" in provider or "wikimedia" in provider for provider in provider_values))
            _append_metric(active_metrics, "Geselecteerde assets", selected, present=selected > 0)
            _append_metric(active_metrics, "Afgewezen assets", rejected, present=rejected > 0)
    elif active_stage_id == "montage":
        _append_metric(active_metrics, "Scènes gepland", len(scenes), present=scenes_manifest_exists)
        shot_planned = sum(len(_as_items(scene.get("shots", []))) for scene in scenes)
        _append_metric(active_metrics, "Shots gepland", shot_planned, present=shot_planned > 0)
        assigned = sum(len(_as_items(scene.get("shots", []))) for scene in timeline_scenes)
        _append_metric(active_metrics, "Shots toegewezen", assigned, present=timeline_manifest_exists and assigned > 0)
    elif active_stage_id == "render":
        current_scene = _event_detail(recent_events, "current_scene")
        total_scenes = _event_detail(recent_events, "total_scenes")
        render_step = _event_detail(recent_events, "render_step", "step")
        _append_metric(active_metrics, "Huidige scène", current_scene, present=current_scene is not None)
        _append_metric(active_metrics, "Totaal scènes", total_scenes, present=total_scenes is not None)
        _append_metric(active_metrics, "Renderstap", render_step, present=render_step is not None)
        render_context_present = str(orchestration.get("current_stage", "")).lower() == "render_video" or any(str(item.get("stage", "")).lower() == "render_video" for item in recent_events) or final_video.exists()
        _append_metric(active_metrics, "Outputbestand", "Gereed" if final_video.exists() else "Nog niet gereed", present=render_context_present)

    activities = [
        {
            "at": str(item.get("at", "")),
            "text": _activity_text(item),
        }
        for item in recent_events
        if _activity_text(item)
    ]
    activities = activities[-12:]

    scene_previews = [
        {
            "scene_id": str(scene.get("id", "")),
            "title": str(scene.get("heading", scene.get("id", "Scène"))),
            "start_seconds": float(scene.get("start_seconds", scene.get("timeline_start_seconds", 0)) or 0),
            "thumbnail_url": f"/projects/{project_root.name}/preview/thumbnail/{str(scene.get('id', ''))}",
        }
        for scene in scenes[:12]
        if isinstance(scene, dict) and scene.get("id")
    ]

    research_events = [
        item
        for item in recent_events
        if _canonical_stage(str(item.get("stage", ""))) == "research"
    ]
    last_research_event = research_events[-1] if research_events else {}
    last_research_completed = next(
        (
            str(item.get("message", "")).strip()
            for item in reversed(research_events)
            if str(item.get("event", "")).strip().lower() == "completed" and str(item.get("message", "")).strip()
        ),
        "",
    )

    if active_stage and active_stage["status"] == "Wacht op jou":
        last_activity = "Wacht op jou"
    elif activities:
        last_activity = activities[-1]["text"]
    else:
        last_activity = "Nog geen activiteit geregistreerd"

    blockers: list[str] = []
    if orchestration.get("last_error"):
        blockers.append(str(orchestration.get("last_error")))
    if repaired:
        blockers.append("Status wordt hersteld")

    queue = TaskQueue(project_root).snapshot()
    status_lower = str(orchestration.get("status", "")).lower()
    waiting_for = str(orchestration.get("waiting_for", "")).lower()
    current_stage_lower = str(orchestration.get("current_stage", "")).lower()
    last_error_lower = str(orchestration.get("last_error", "")).lower()
    paid_gate_required = (
        status_lower in {"approval_required", "waiting_for_approval"}
        or (
            status_lower == "blocked"
            and (
                "paid api" in last_error_lower
                or "toestemming" in last_error_lower
                or "approval" in last_error_lower
                or current_stage_lower in {"research", "research_plan", "research_approval"}
            )
        )
    ) and (
        not waiting_for
        or waiting_for in {"research", "research_plan", "approve_research", "research_approval"}
    )

    approval_path = manifests / "paid_research_approval.json"
    approval = _read_manifest(approval_path, {})
    estimate = _read_manifest(manifests / "cost_estimate.json", {})
    estimated_cost = float(approval.get("estimated_cost_usd") or 0.0)
    if estimated_cost <= 0:
        estimated_cost = float(
            sum(
                float(item.get("estimated_maximum_cost_usd", 0) or 0)
                for item in estimate.get("stages", [])
                if str(item.get("stage", "")).strip().lower() in {"research_plan", "source_analysis", "tavily_research"}
            )
        )
    project_budget = float(estimate.get("project_budget_usd", 0) or 0)
    within_budget = project_budget <= 0 or estimated_cost <= project_budget

    plan_payload = _read_manifest(manifests / "research_plan.json", {})
    unapproved_claims = [str(item.get("text")) for item in claims if str(item.get("review_status", "")).lower() != "approved" and item.get("text")]
    if not unapproved_claims:
        unapproved_claims = [str(item.get("text")) for item in claims[:5] if item.get("text")]
    gate_claims = [str(item) for item in approval.get("claims", []) if str(item).strip()] or unapproved_claims
    gate_countries = [str(item) for item in approval.get("countries", []) if str(item).strip()]
    if not gate_countries:
        gate_countries = [
            str(item.get("country"))
            for item in plan_payload.get("involved_countries", [])
            if isinstance(item, dict) and str(item.get("country", "")).strip()
        ]
    gate_languages = [str(item) for item in approval.get("languages", []) if str(item).strip()] or [str(item) for item in plan_payload.get("relevant_languages", []) if str(item).strip()]
    if not gate_languages:
        gate_languages = [str(workflow.get("language") or "Nederlands")]
    gate_operations = [
        str(item.get("stage"))
        for item in estimate.get("stages", [])
        if str(item.get("stage", "")).strip().lower() in {"research_plan", "source_analysis"}
    ]
    if "tavily_research" not in gate_operations:
        gate_operations.append("tavily_research")

    feature_flags = {
        "recycle_mode": workflow.get("workflow_type") == "recycle_documentary",
        "reference_video": (manifests / "reference_documentary.json").exists(),
        "ai_edits": final_video.exists(),
        "revision_history": bool(_as_items(editor_workspace_manifest.get("revisions", []))),
        "subtitles_enabled": bool(editor_workspace_manifest.get("timeline", {}).get("subtitles_enabled")) if isinstance(editor_workspace_manifest.get("timeline"), dict) else False,
        "branding_enabled": bool(visual_style.get("branding", {}).get("enabled")) if isinstance(visual_style.get("branding"), dict) else False,
    }

    actions: list[dict[str, str]] = []
    if final_video.exists():
        slug = project_root.name
        actions.extend(
            [
                {"label": "Video bekijken", "url": f"/projects/{slug}/preview/video", "kind": "link"},
                {"label": "Video bewerken", "url": f"/projects/{slug}/editor", "kind": "link"},
                {"label": "Nieuwe versie renderen", "url": f"/projects/{slug}/editor/render", "kind": "post"},
                {"label": "Recycle-documentaire maken", "url": "/projects/new?workflow_type=recycle_documentary", "kind": "link"},
            ]
        )

    monitor_message = _monitor_message(active_stage_id, active_metrics)

    resolution = str(approval.get("resolution", "")).strip().lower()
    if resolution in {"approved", "cancelled", "local_fallback"} or approval.get("approval_required") is False:
        paid_gate_required = False

    phases_payload: list[dict[str, Any]] = []
    for stage in normalized_stages:
        status_label = str(stage.get("status", ""))
        status_code = (
            "completed" if status_label == "Klaar"
            else "in_progress" if status_label == "Bezig"
            else "approval_required" if status_label == "Wacht op jou"
            else "blocked" if status_label in {"Geblokkeerd", "Mislukt"}
            else "pending"
        )
        progress_value = 100 if status_code == "completed" else 0
        phases_payload.append({
            "id": stage.get("id", ""),
            "name": stage.get("name", ""),
            "status": status_code,
            "progress": progress_value,
            "provider": "local",
            "estimated_cost_usd": 0.0,
            "artifacts": [],
        })
    if paid_gate_required:
        for phase in phases_payload:
            if phase.get("id") == "research":
                phase["status"] = "approval_required"
                phase["progress"] = 0
                break

    return {
        "stages": normalized_stages,
        "phases": phases_payload,
        "percentage": percentage,
        "progress_preparing": percentage is None,
        "remaining_steps": max(0, len(CANONICAL_WORKFLOW) - completed_count),
        "current_phase": current_phase,
        "estimated_remaining": "" if percentage is None or paid_gate_required else f"ongeveer {max(0, len(CANONICAL_WORKFLOW) - completed_count) * 3} minuten",
        "last_activity": last_activity,
        "last_update": orchestration.get("updated_at") or (recent_events[-1].get("at") if recent_events else "Nog geen update"),
        "events": recent_events,
        "activity": activities,
        "active_stage_metrics": active_metrics,
        "status_repair_message": "Status wordt hersteld" if repaired else "",
        "blockers": blockers,
        "queue": queue,
        "paid_gate": {
            "required": paid_gate_required,
            "title": "Onderzoek wacht op jouw toestemming",
            "within_budget": within_budget,
            "local_fallback_available": bool(sources and claims),
            "claims": gate_claims,
            "countries": gate_countries,
            "languages": gate_languages,
            "maximum_cost_usd": round(estimated_cost, 6),
            "extra_sources": int(approval.get("extra_sources", 5) or 5),
            "provider": "tavily",
            "purpose": str(approval.get("reason") or "Aanvullend onderzoek vereist toestemming."),
            "operations": gate_operations,
        },
        "research": {
            "current_task": str(last_research_event.get("message", "")).strip() or "Nog geen activiteit geregistreerd",
            "last_completed": last_research_completed or "Nog geen activiteit geregistreerd",
            "sources_found": len(sources) if source_manifest_exists else None,
            "sources_processed": research_sources_processed if source_manifest_exists else None,
            "draft_claims": len(claims) if claim_manifest_exists else None,
            "claims_awaiting_verification": claims_unverified if claim_manifest_exists else None,
        },
        "feature_flags": feature_flags,
        "actions": actions,
        "monitor_message": monitor_message,
        "scene_previews": scene_previews,
    }


def supported_script_map(project_root: Path) -> list[dict[str, Any]]:
    claims = {str(item.get("id")): item for item in read_json(project_root / "manifests/claims.json").get("claims", [])} if (project_root / "manifests/claims.json").exists() else {}
    scenes = read_json(project_root / "manifests/scenes.json").get("scenes", []) if (project_root / "manifests/scenes.json").exists() else []
    return [{"scene_id": scene.get("id"), "script": scene.get("narration", ""), "claims": [claims[cid] for cid in map(str, scene.get("claim_ids", [])) if cid in claims]} for scene in scenes]


def apply_dossier_instruction(project_root: Path, instruction: str, *, item_id: str = "") -> dict[str, Any]:
    lowered = instruction.lower().strip()
    claims_path, sources_path = project_root / "manifests/claims.json", project_root / "manifests/sources.json"
    claims = read_json(claims_path) if claims_path.exists() else {"claims": []}
    sources = read_json(sources_path) if sources_path.exists() else {"sources": []}
    action = "emphasis"
    changed = []
    if "verwijder" in lowered:
        action = "reject_claim"
        for claim in claims.get("claims", []):
            if str(claim.get("id")) == item_id:
                claim["review_status"] = "rejected"; changed.append(item_id)
    elif "bron niet" in lowered or "gebruik deze bron niet" in lowered:
        action = "reject_source"
        for source in sources.get("sources", []):
            if str(source.get("id")) == item_id:
                source["review_status"] = "rejected"; changed.append(item_id)
    elif "onderzoek" in lowered:
        action = "research_followup"
        queue = read_json(project_root / "manifests/research_followups.json") if (project_root / "manifests/research_followups.json").exists() else {"items": []}
        key = f"followup-{len(queue['items']) + 1}"
        if not any(item.get("instruction") == instruction for item in queue["items"]):
            queue["items"].append({"id": key, "instruction": instruction, "status": "queued"})
        write_json(project_root / "manifests/research_followups.json", queue)
    else:
        emphasis = read_json(project_root / "manifests/research_emphasis.json") if (project_root / "manifests/research_emphasis.json").exists() else {"instructions": []}
        if instruction not in emphasis["instructions"]:
            emphasis["instructions"].append(instruction)
        write_json(project_root / "manifests/research_emphasis.json", emphasis)
    write_json(claims_path, claims); write_json(sources_path, sources)
    return {"action": action, "changed_ids": changed, "instruction": instruction}


def revision_change_plan(project_root: Path, command: str, scene_id: str | None = None) -> dict[str, Any]:
    draft = create_review_draft(project_root)
    lowered = command.lower()
    if scene_id:
        targets = [scene_id]
    elif match := re.search(r"sc[eè]ne\s+(\d+)", lowered):
        targets = [str(scene["id"]) for scene in draft["scenes"] if int(scene.get("index") or 0) == int(match.group(1))]
    elif "intro" in lowered:
        targets = [str(draft["scenes"][0]["id"])]
    elif "outro" in lowered:
        targets = [str(draft["scenes"][-1]["id"])]
    else:
        raise ValueError("Selecteer een scène of noem deze in het verzoek.")
    components = []
    mapping = (("beeld", "media"), ("screenshot", "media"), ("zin", "script"), ("context", "script"), ("voice-over", "voice_over"), ("interview", "clips"), ("verkort", "timing"), ("spannender", "producer"), ("krachtiger", "producer"))
    for term, component in mapping:
        if term in lowered and component not in components:
            components.append(component)
    if not components:
        components = ["script", "producer", "director"]
    cost = round(sum({"script": .01, "media": .03, "voice_over": .02, "clips": .01, "timing": 0, "producer": 0, "director": 0}.get(c, 0) for c in components), 2)
    plan = {"version": 1, "command": command, "scene_ids": targets, "components": components, "estimated_cost_usd": cost, "status": "awaiting_confirmation", "created_at": datetime.now(UTC).isoformat()}
    write_json(project_root / "manifests/pending_revision_plan.json", plan)
    return plan


def youtube_draft(project_root: Path) -> dict[str, Any]:
    path = project_root / "manifests/youtube_draft.json"
    if path.exists():
        return read_json(path)
    project = read_json(project_root / "manifests/project.json")
    scenes = read_json(project_root / "manifests/scenes.json").get("scenes", []) if (project_root / "manifests/scenes.json").exists() else []
    chapters, cursor = [], 0.0
    for scene in scenes:
        chapters.append({"start_seconds": round(cursor, 1), "title": scene.get("heading", scene.get("id"))})
        cursor += float(scene.get("duration_seconds", scene.get("estimated_duration_seconds", 0)))
    payload = {"version": 1, "status": "draft", "title": project.get("topic", project_root.name), "description": "Conceptdocumentaire — controleer bronnen en rechten voor publicatie.", "chapters": chapters, "tags": ["documentaire", "onderzoek"], "thumbnail": "assets/thumbnails/scene-01.png", "subtitles": "manifests/subtitles.srt", "video": "exports/final_video.mp4", "privacy_status": "private", "upload_confirmed": False}
    write_json(path, payload)
    return payload
