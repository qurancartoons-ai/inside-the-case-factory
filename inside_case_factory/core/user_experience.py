from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from inside_case_factory.core.draft_review import create_review_draft
from inside_case_factory.core.progress import TaskQueue
from inside_case_factory.utils.files import read_json, write_json


PHASES = ("Intake", "Onderzoek", "Claims", "Script", "Producer", "Director", "Media", "Voice-over", "Montage", "Review")


def production_progress(project_root: Path) -> dict[str, Any]:
    manifests = project_root / "manifests"
    usage = read_json(manifests / "provider_usage.json") if (manifests / "provider_usage.json").exists() else {"spent_usd": 0, "calls": []}
    selections = read_json(manifests / "provider_selection.json").get("selections", {}) if (manifests / "provider_selection.json").exists() else {}
    activity = read_json(manifests / "production_activity.json") if (manifests / "production_activity.json").exists() else {}
    phase_files = {
        "Intake": ["project.json"], "Onderzoek": ["dossier.json"], "Claims": ["claims.json"], "Script": ["script.json"],
        "Producer": ["producer_blueprint.json"], "Director": ["director_plan.json"], "Media": ["media_sources.json"],
        "Voice-over": ["narration_timing.json"], "Montage": ["../exports/final_video.mp4"], "Review": ["review_draft.json"],
    }
    errors = []
    orchestration = read_json(manifests / "orchestration.json") if (manifests / "orchestration.json").exists() else {}
    if orchestration.get("last_error"):
        errors.append(str(orchestration["last_error"]))
    phases = []
    first_missing = True
    for phase in PHASES:
        artifacts = [path for path in phase_files[phase] if (manifests / path).exists()]
        complete = len(artifacts) == len(phase_files[phase])
        status = "afgerond" if complete else "actief" if first_missing else "wachtend"
        if not complete and first_missing:
            first_missing = False
        task = {"Producer": "producer_blueprint", "Director": "director_plan", "Voice-over": "voice_over", "Media": "scene_image", "Critic": "critic_review"}.get(phase, "")
        phases.append({
            "name": phase, "status": status, "progress": 100 if complete else 35 if status == "actief" else 0,
            "provider": selections.get(task, {}).get("provider", "offline/local"),
            "estimated_cost_usd": round(sum(float(call.get("cost_usd", 0)) for call in usage.get("calls", []) if call.get("task") == task), 4),
            "artifacts": artifacts, "errors": errors if status == "actief" else [],
        })
    confirmation = read_json(manifests / "paid_api_confirmation.json") if (manifests / "paid_api_confirmation.json").exists() else {}
    estimate = read_json(manifests / "cost_estimate.json") if (manifests / "cost_estimate.json").exists() else {}
    research_cost = round(sum(float(item.get("estimated_maximum_cost_usd", 0)) for item in estimate.get("stages", []) if item.get("stage") in {"research_plan", "source_analysis"}), 6)
    confirmed = bool(confirmation.get("confirmed") is True and ("project" not in confirmation or confirmation.get("project") == project_root.name) and ("approved_limit_usd" not in confirmation or float(confirmation.get("approved_limit_usd", 0)) + 1e-9 >= research_cost))
    confirmation_error = "paid api call not confirmed" in str(orchestration.get("last_error", "")).lower()
    cancelled = confirmation.get("cancelled") is True
    paid_gate = bool(estimate and not confirmed and not cancelled and (confirmation_error or orchestration.get("current_stage") in {"", "research_plan", "research"}))
    if paid_gate:
        gate_index = PHASES.index("Onderzoek")
        for index, phase in enumerate(phases):
            if index < gate_index:
                phase.update({"status": "afgerond", "progress": 100})
            elif index == gate_index:
                phase.update({"status": "approval_required", "progress": 0, "errors": []})
            else:
                phase.update({"status": "wachtend", "progress": 0, "errors": []})
    pipeline_blocked = orchestration.get("status") == "blocked" and not paid_gate
    if pipeline_blocked:
        gate_index = PHASES.index("Onderzoek")
        for index, phase in enumerate(phases):
            if index < gate_index: phase.update({"status": "afgerond", "progress": 100})
            elif index == gate_index: phase.update({"status": "blocked", "progress": 0, "errors": errors})
            else: phase.update({"status": "wachtend", "progress": 0, "errors": []})
    workflow = read_json(manifests / "workflow.json") if (manifests / "workflow.json").exists() else {}
    approvals = [name for name, required in (
        ("research", not workflow.get("research_approved")), ("script", not workflow.get("script_approved")),
        ("draft scenes", any(scene.get("review_status") != "approved" for scene in (read_json(manifests / "review_draft.json").get("scenes", []) if (manifests / "review_draft.json").exists() else []))),
    ) if required]
    queue = TaskQueue(project_root).snapshot()
    events_data = read_json(manifests / "progress_events.json") if (manifests / "progress_events.json").exists() else {"events": []}
    events = events_data.get("events", [])[-20:]
    active_phase = next((phase for phase in phases if phase["status"] in {"actief", "approval_required", "blocked", "fout"}), phases[-1])
    completed = sum(phase["status"] == "afgerond" for phase in phases)
    source_data = read_json(manifests / "sources.json") if (manifests / "sources.json").exists() else {"sources": []}
    claim_data = read_json(manifests / "claims.json") if (manifests / "claims.json").exists() else {"claims": []}
    sources = source_data.get("sources", []) if isinstance(source_data, dict) else []
    claims = claim_data.get("claims", []) if isinstance(claim_data, dict) else []
    research_events = [item for item in events if item.get("stage") in {"research", "research_plan", "Onderzoek"}]
    last_research = research_events[-1] if research_events else {}
    research = {
        "current_task": last_research.get("message") or ("Wacht op jouw controle" if approvals else "Onderzoek voorbereiden"),
        "last_completed": next((item.get("message") for item in reversed(research_events) if item.get("event") == "completed"), "Nog geen actie afgerond"),
        "sources_found": len(sources), "sources_processed": sum(bool(item.get("summary") or item.get("review_status")) for item in sources),
        "draft_claims": sum(item.get("review_status") != "approved" for item in claims),
        "waiting_since": last_research.get("at", activity.get("log", [{}])[-1].get("at", "") if activity.get("log") else ""),
        "provider": str(source_data.get("provider") or orchestration.get("provider") or "Lokaal"),
        "estimated_remaining": "" if paid_gate or pipeline_blocked else ("Minder dan 5 minuten" if sources else "Nog te bepalen"),
        "last_error": str(orchestration.get("last_error") or ""),
    }
    provider = "OpenAI en Tavily"
    local_fallback = bool(sources and claims)
    within_budget = research_cost <= float(estimate.get("project_budget_usd", 0) or 0)
    gate = {"required": paid_gate, "title": "Onderzoek wacht op jouw toestemming", "maximum_cost_usd": research_cost, "provider": provider, "purpose": "Een onderzoeksplan maken en bronnen omzetten in controleerbare claims.", "operations": ["research_plan", "source_analysis", "tavily_research"], "local_fallback_available": local_fallback, "within_budget": within_budget}
    return {"phases": phases, "percentage": round(completed / len(phases) * 100), "remaining_steps": len(phases) - completed, "current_phase": active_phase["name"], "estimated_remaining": "" if paid_gate or pipeline_blocked else f"ongeveer {max(1, len(phases)-completed) * 3} minuten", "cost_usd": float(usage.get("spent_usd", 0)), "last_activity": "Wacht op jouw toestemming" if paid_gate else activity.get("current_activity", "Nog geen activiteit"), "last_update": orchestration.get("updated_at") or (events[-1].get("at") if events else "Nog geen update"), "approvals": approvals, "blockers": errors, "queue": queue, "events": events, "research": research, "paid_gate": gate}


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
