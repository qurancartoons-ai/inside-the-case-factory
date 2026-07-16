from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from inside_case_factory.core.autonomous_direction import CriticEngine, DirectorEngine
from inside_case_factory.core.producer import ProducerEngine
from inside_case_factory.utils.files import read_json, write_json


def _optional(project_root: Path, name: str) -> dict[str, Any]:
    path = project_root / "manifests" / name
    return read_json(path) if path.exists() else {}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _assets_for_scene(assets: list[dict[str, Any]], scene_id: str) -> list[dict[str, Any]]:
    return [asset for asset in assets if scene_id in {str(item) for item in asset.get("mapped_scenes", [])} or asset.get("mapped_scenes") == ["*"]]


def create_review_draft(project_root: Path) -> dict[str, Any]:
    """Materialize a review snapshot from dossier and render manifests."""
    scenes_manifest = _optional(project_root, "scenes.json")
    claims = {str(item.get("id")): item for item in _optional(project_root, "claims.json").get("claims", [])}
    sources = {str(item.get("id")): item for item in _optional(project_root, "sources.json").get("sources", [])}
    assets = _optional(project_root, "media_sources.json").get("assets", [])
    clip_sources = _optional(project_root, "clip_sources.json").get("clips", [])
    direction = {str(item.get("scene_id")): item for item in _optional(project_root, "director_plan.json").get("scenes", [])}
    producer = {str(item.get("id")): item for item in _optional(project_root, "producer_blueprint.json").get("sections", [])}
    previous = _optional(project_root, "review_draft.json")
    previous_states = {str(item.get("id")): item for item in previous.get("scenes", [])}
    review_scenes: list[dict[str, Any]] = []
    for scene in scenes_manifest.get("scenes", []):
        scene_id = str(scene.get("id"))
        used_claims = [claims[cid] for cid in map(str, scene.get("claim_ids", [])) if cid in claims]
        source_ids = {str(source_id) for claim in used_claims for source_id in claim.get("source_ids", [])}
        media = _assets_for_scene(assets, scene_id)
        clips = [clip for clip in clip_sources if scene_id in {str(item) for item in clip.get("scene_ids", [])}]
        current = {
            "id": scene_id, "index": scene.get("index"), "heading": scene.get("heading", f"Scène {scene.get('index', '')}"),
            "script": scene.get("narration", ""), "voice_over_text": scene.get("narration", ""),
            "voice_over_delivery": scene.get("voice_over_delivery", "neutral_documentary"),
            "estimated_duration_seconds": scene.get("duration_seconds", scene.get("estimated_duration_seconds", 0)),
            "claims": used_claims, "sources": [sources[source_id] for source_id in source_ids if source_id in sources],
            "media": media, "clips": clips, "camera_direction": direction.get(scene_id, {}),
            "edit_plan": {"producer": producer.get(scene_id, {}), "director": direction.get(scene_id, {})},
            "revision_directives": scene.get("revision_directives", []),
        }
        old = previous_states.get(scene_id, {})
        current["review_status"] = old.get("review_status", "pending_review")
        current["approved_fingerprint"] = old.get("approved_fingerprint")
        current["revision_number"] = old.get("revision_number", 0)
        review_scenes.append(current)
    payload = {
        "version": 1, "status": "reviewable_draft", "updated_at": datetime.now(UTC).isoformat(),
        "dossier": _optional(project_root, "dossier.json"), "video": {
            "path": "exports/final_video.mp4", "available": (project_root / "exports/final_video.mp4").exists()},
        "scenes": review_scenes, "revision_history": previous.get("revision_history", []),
    }
    write_json(project_root / "manifests" / "review_draft.json", payload)
    return payload


def approve_scene(project_root: Path, scene_id: str) -> dict[str, Any]:
    draft = create_review_draft(project_root)
    for scene in draft["scenes"]:
        if scene["id"] == scene_id:
            scene["review_status"] = "approved"
            scene["approved_fingerprint"] = _fingerprint({k: v for k, v in scene.items() if k not in {"review_status", "approved_fingerprint"}})
            scene["approved_at"] = datetime.now(UTC).isoformat()
            write_json(project_root / "manifests" / "review_draft.json", draft)
            return scene
    raise KeyError(f"Unknown scene: {scene_id}")


def _target_ids(command: str, scenes: list[dict[str, Any]], selected_scene_id: str | None) -> list[str]:
    lowered = command.lower()
    match = re.search(r"sc[eè]ne\s+(\d+)", lowered)
    if match:
        index = int(match.group(1))
        return [str(scene["id"]) for scene in scenes if int(scene.get("index") or 0) == index]
    if "intro" in lowered:
        return [str(scenes[0]["id"])] if scenes else []
    if "outro" in lowered or "afsluiting" in lowered:
        return [str(scenes[-1]["id"])] if scenes else []
    if selected_scene_id:
        return [selected_scene_id]
    raise ValueError("No scene could be inferred; select a scene or name one in the revision request.")


def _directives(command: str) -> list[dict[str, Any]]:
    lowered = command.lower()
    directives: list[dict[str, Any]] = []
    seconds = re.search(r"verkort.+?(\d+)\s*secon", lowered)
    if seconds:
        directives.append({"kind": "shorten", "seconds": int(seconds.group(1)), "components": ["script", "voice_over", "edit"]})
    if "spannender" in lowered:
        directives.append({"kind": "tone", "value": "more_tense", "components": ["script", "voice_over", "producer"]})
    if "emotioneler" in lowered:
        directives.append({"kind": "voice_delivery", "value": "more_emotional", "components": ["voice_over"]})
    if "close-up" in lowered or "closeups" in lowered or "close-ups" in lowered:
        directives.append({"kind": "camera", "value": "more_close_ups", "components": ["director", "edit"]})
    if "krachtiger" in lowered and ("outro" in lowered or "eind" in lowered):
        directives.append({"kind": "ending", "value": "stronger_resolved_ending", "components": ["script", "voice_over", "producer"]})
    if "minder beelden" in lowered:
        subject = lowered.split("minder beelden", 1)[1].strip(" .") or "specified subject"
        directives.append({"kind": "reduce_media", "query": subject, "components": ["media", "director", "edit"]})
    interview = re.search(r"voeg (?:hier )?het (.+?interview) toe", lowered)
    if interview:
        directives.append({"kind": "add_interview", "query": interview.group(1), "components": ["clips", "script", "edit"]})
    if "mijn screenshot" in lowered:
        directives.append({"kind": "add_screenshot", "query": "user screenshot", "components": ["media", "director", "edit"]})
    if not directives:
        directives.append({"kind": "natural_revision", "instruction": command.strip(), "components": ["script", "producer", "director"]})
    return directives


def revise_draft(project_root: Path, command: str, *, selected_scene_id: str | None = None) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("Revision request is empty.")
    draft = create_review_draft(project_root)
    targets = _target_ids(command, draft["scenes"], selected_scene_id)
    directives = _directives(command)
    idempotency_key = _fingerprint({"command": command.strip().lower(), "scene_ids": targets})
    for previous in reversed(draft.get("revision_history", [])):
        if previous.get("idempotency_key") == idempotency_key:
            return previous
    scene_manifest = _optional(project_root, "scenes.json")
    media_manifest = _optional(project_root, "media_sources.json")
    clip_manifest = _optional(project_root, "clip_sources.json")
    before = deepcopy(scene_manifest.get("scenes", []))
    changed: list[str] = []
    components: set[str] = set()
    for scene in scene_manifest.get("scenes", []):
        scene_id = str(scene.get("id"))
        if scene_id not in targets:
            continue
        review_scene = next(item for item in draft["scenes"] if item["id"] == scene_id)
        if review_scene.get("review_status") == "approved":
            raise RuntimeError(f"Scene {scene_id} is approved and locked; reopen it before revision.")
        scene.setdefault("revision_directives", []).extend(directives)
        for directive in directives:
            components.update(directive["components"])
            if directive["kind"] == "shorten":
                key = "duration_seconds" if "duration_seconds" in scene else "estimated_duration_seconds"
                old_duration = max(2.0, float(scene.get(key, 10)))
                new_duration = max(2.0, old_duration - float(directive["seconds"]))
                words = str(scene.get("narration", "")).split()
                keep = max(1, round(len(words) * new_duration / old_duration)) if words else 0
                if words:
                    scene["narration"] = " ".join(words[:keep])
                scene[key] = new_duration
            elif directive["kind"] == "voice_delivery":
                scene["voice_over_delivery"] = directive["value"]
            elif directive["kind"] == "camera":
                scene["camera_preference"] = directive["value"]
            elif directive["kind"] == "tone":
                scene["narrative_tone"] = directive["value"]
                scene["script_revision_brief"] = "Increase tension through structure and cadence without adding unsupported facts."
            elif directive["kind"] == "ending":
                scene["ending_style"] = directive["value"]
                scene["script_revision_brief"] = "End decisively on the strongest supported conclusion without overstating certainty."
            elif directive["kind"] == "add_screenshot":
                candidates = [asset for asset in media_manifest.get("assets", []) if "screenshot" in " ".join(str(asset.get(key, "")) for key in ("id", "title", "path", "usage_notes")).lower()]
                if not candidates:
                    raise RuntimeError("No user screenshot is available in the media manifest.")
                mapped = candidates[0].setdefault("mapped_scenes", [])
                if scene_id not in mapped:
                    mapped.append(scene_id)
            elif directive["kind"] == "add_interview":
                query_tokens = {token for token in re.findall(r"\w+", directive["query"].lower()) if token != "interview"}
                candidates = [clip for clip in clip_manifest.get("clips", []) if query_tokens & set(re.findall(r"\w+", " ".join(str(clip.get(key, "")) for key in ("video_title", "channel", "source_url")).lower()))]
                if not candidates:
                    raise RuntimeError(f"No matching interview is available: {directive['query']}")
                mapped = candidates[0].setdefault("scene_ids", [])
                if scene_id not in mapped:
                    mapped.append(scene_id)
            elif directive["kind"] == "reduce_media":
                query_tokens = set(re.findall(r"\w+", directive["query"].lower()))
                for asset in media_manifest.get("assets", []):
                    haystack = set(re.findall(r"\w+", " ".join(str(asset.get(key, "")) for key in ("id", "title", "path", "scene_relevance")).lower()))
                    if query_tokens & haystack:
                        asset["mapped_scenes"] = [item for item in asset.get("mapped_scenes", []) if str(item) != scene_id]
        changed.append(scene_id)
    if not changed:
        raise KeyError("Selected scene does not exist.")
    write_json(project_root / "manifests" / "scenes.json", scene_manifest)
    if media_manifest:
        write_json(project_root / "manifests" / "media_sources.json", media_manifest)
    if clip_manifest:
        write_json(project_root / "manifests" / "clip_sources.json", clip_manifest)
    evaluations = {
        "producer": ProducerEngine().review_revisions(project_root, scene_manifest["scenes"], changed),
        "director": DirectorEngine().review_revisions(project_root, scene_manifest["scenes"], changed),
        "critic": CriticEngine().review_revisions(project_root, scene_manifest["scenes"], changed),
    }
    draft = create_review_draft(project_root)
    revision_id = f"revision-{len(draft.get('revision_history', [])) + 1}"
    for scene in draft["scenes"]:
        if scene["id"] in changed:
            scene["review_status"] = "revised_pending_review"
            scene["revision_number"] = int(scene.get("revision_number", 0)) + 1
    unchanged_preserved = all(
        original == current for original, current in zip(before, scene_manifest["scenes"], strict=True)
        if str(original.get("id")) not in changed
    )
    entry = {
        "id": revision_id, "requested_at": datetime.now(UTC).isoformat(), "command": command,
        "idempotency_key": idempotency_key,
        "changed_scene_ids": changed, "regenerated_components": sorted(components),
        "unchanged_scenes_preserved": unchanged_preserved, "evaluations": evaluations,
    }
    draft.setdefault("revision_history", []).append(entry)
    write_json(project_root / "manifests" / "review_draft.json", draft)
    write_json(project_root / "manifests" / "selective_regeneration.json", {
        "version": 1, "status": "draft_render_required", "revision_id": revision_id,
        "scene_ids": changed, "components": sorted(components), "preserve_approved": True,
    })
    return entry
