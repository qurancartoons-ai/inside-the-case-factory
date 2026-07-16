from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from inside_case_factory.core.draft_review import create_review_draft
from inside_case_factory.utils.files import read_json, write_json


PHASES = ("Intake", "Research", "Claims", "Script", "Producer", "Director", "Media", "Voice-over", "Render", "Critic", "Draft Review")


def production_progress(project_root: Path) -> dict[str, Any]:
    manifests = project_root / "manifests"
    usage = read_json(manifests / "provider_usage.json") if (manifests / "provider_usage.json").exists() else {"spent_usd": 0, "calls": []}
    selections = read_json(manifests / "provider_selection.json").get("selections", {}) if (manifests / "provider_selection.json").exists() else {}
    activity = read_json(manifests / "production_activity.json") if (manifests / "production_activity.json").exists() else {}
    phase_files = {
        "Intake": ["project.json"], "Research": ["dossier.json"], "Claims": ["claims.json"], "Script": ["script.json"],
        "Producer": ["producer_blueprint.json"], "Director": ["director_plan.json"], "Media": ["media_sources.json"],
        "Voice-over": ["narration_timing.json"], "Render": ["../exports/final_video.mp4"], "Critic": ["critic_report.json"],
        "Draft Review": ["review_draft.json"],
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
        status = "completed" if complete else "active" if first_missing else "waiting"
        if not complete and first_missing:
            first_missing = False
        task = {"Producer": "producer_blueprint", "Director": "director_plan", "Voice-over": "voice_over", "Media": "scene_image", "Critic": "critic_review"}.get(phase, "")
        phases.append({
            "name": phase, "status": status, "progress": 100 if complete else 35 if status == "active" else 0,
            "provider": selections.get(task, {}).get("provider", "offline/local"),
            "estimated_cost_usd": round(sum(float(call.get("cost_usd", 0)) for call in usage.get("calls", []) if call.get("task") == task), 4),
            "artifacts": artifacts, "errors": errors if status == "active" else [],
        })
    workflow = read_json(manifests / "workflow.json") if (manifests / "workflow.json").exists() else {}
    approvals = [name for name, required in (
        ("research", not workflow.get("research_approved")), ("script", not workflow.get("script_approved")),
        ("draft scenes", any(scene.get("review_status") != "approved" for scene in (read_json(manifests / "review_draft.json").get("scenes", []) if (manifests / "review_draft.json").exists() else []))),
    ) if required]
    return {"phases": phases, "cost_usd": float(usage.get("spent_usd", 0)), "last_activity": activity.get("current_activity", "Nog geen activiteit"), "approvals": approvals, "blockers": errors}


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
