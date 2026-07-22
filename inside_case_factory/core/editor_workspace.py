from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import copy
import json
import re
import shutil
from typing import Any

from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace


EDITOR_HISTORY = "editor_history.json"
EDITOR_PENDING_PLAN = "editor_pending_plan.json"
EDITOR_CANDIDATES = "editor_media_candidates.json"
REVISION_DIR = "editor_revisions"

SNAPSHOT_FILES = (
    "scenes.json",
    "visual_direction.json",
    "subtitles.json",
    "visual_style_profile.json",
    "narration_timing.json",
    "media_sources.json",
)

MOTION_MAP = {
    "static": "static",
    "slow_zoom": "slow_zoom_in",
    "slow_zoom_in": "slow_zoom_in",
    "slow_zoom_out": "slow_zoom_out",
    "push_in": "controlled_push_in",
    "controlled_push_in": "controlled_push_in",
    "pan": "parallax",
    "parallax": "parallax",
}


class EditorError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _manifests(project_root: Path) -> Path:
    return project_root / "manifests"


def _history_path(project_root: Path) -> Path:
    return _manifests(project_root) / EDITOR_HISTORY


def _pending_plan_path(project_root: Path) -> Path:
    return _manifests(project_root) / EDITOR_PENDING_PLAN


def _candidates_path(project_root: Path) -> Path:
    return _manifests(project_root) / EDITOR_CANDIDATES


def _revision_root(project_root: Path) -> Path:
    return _manifests(project_root) / REVISION_DIR


def _read_manifest(project_root: Path, name: str) -> dict[str, Any]:
    path = _manifests(project_root) / name
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _load_history(project_root: Path) -> dict[str, Any]:
    path = _history_path(project_root)
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _save_history(project_root: Path, history: dict[str, Any]) -> None:
    write_json(_history_path(project_root), history)


def _revision_snapshot_dir(project_root: Path, revision_id: str) -> Path:
    return _revision_root(project_root) / revision_id


def _copy_in(path_from: Path, path_to: Path) -> None:
    path_to.parent.mkdir(parents=True, exist_ok=True)
    if path_from.exists():
        shutil.copy2(path_from, path_to)


def _create_snapshot(project_root: Path, revision_id: str) -> None:
    target = _revision_snapshot_dir(project_root, revision_id)
    target.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_FILES:
        _copy_in(_manifests(project_root) / name, target / name)


def _restore_snapshot(project_root: Path, revision_id: str) -> None:
    source = _revision_snapshot_dir(project_root, revision_id)
    if not source.exists():
        raise EditorError("Revision snapshot is missing.")
    for name in SNAPSHOT_FILES:
        src = source / name
        dst = _manifests(project_root) / name
        if src.exists():
            _copy_in(src, dst)


def _baseline_history(project_root: Path) -> dict[str, Any]:
    revision_id = "rev000"
    _create_snapshot(project_root, revision_id)
    return {
        "version": 1,
        "created_at": _now(),
        "current_index": 0,
        "revisions": [
            {
                "id": revision_id,
                "label": "Original",
                "created_at": _now(),
                "operation_type": "baseline",
                "operation_summary": "Original completed documentary.",
                "snapshot_dir": f"{REVISION_DIR}/{revision_id}",
                "duration_delta_seconds": 0.0,
            }
        ],
    }


def ensure_editor_workspace(project_root: Path) -> dict[str, Any]:
    manifests = _manifests(project_root)
    manifests.mkdir(parents=True, exist_ok=True)
    history = _load_history(project_root)
    if not history:
        history = _baseline_history(project_root)
        _save_history(project_root, history)

    style = _read_manifest(project_root, "visual_style_profile.json")
    subtitles = style.get("subtitles", {}) if isinstance(style.get("subtitles"), dict) else {}
    if "enabled" not in subtitles:
        subtitles["enabled"] = False
    if "max_lines" not in subtitles:
        subtitles["max_lines"] = 2
    style["subtitles"] = subtitles
    branding = style.get("branding", {}) if isinstance(style.get("branding"), dict) else {}
    if "enabled" not in branding:
        branding["enabled"] = False
        branding["text"] = ""
        branding["opacity"] = 0.0
    style["branding"] = branding
    write_json(manifests / "visual_style_profile.json", style)
    return history


def _scene_duration(scene: dict[str, Any], timing_segment: dict[str, Any] | None = None) -> float:
    if isinstance(timing_segment, dict):
        value = float(timing_segment.get("duration_seconds", 0.0) or 0.0)
        if value > 0:
            return value
    for key in ("duration_seconds", "estimated_duration_seconds"):
        value = float(scene.get(key, 0.0) or 0.0)
        if value > 0:
            return value
    return 8.0


def _normalize_shot_durations(shots: list[dict[str, Any]], target_duration: float) -> None:
    if not shots:
        return
    minimum = 0.7
    for shot in shots:
        current = float(shot.get("duration_seconds", minimum) or minimum)
        shot["duration_seconds"] = max(minimum, current)
    total = sum(float(shot.get("duration_seconds", minimum) or minimum) for shot in shots)
    if total <= 0:
        equal = max(minimum, target_duration / max(1, len(shots)))
        for shot in shots:
            shot["duration_seconds"] = round(equal, 3)
        return
    scale = target_duration / total if target_duration > 0 else 1.0
    for shot in shots:
        shot["duration_seconds"] = round(max(minimum, float(shot.get("duration_seconds", minimum)) * scale), 3)
    corrected = sum(float(shot.get("duration_seconds", minimum)) for shot in shots)
    delta = round(target_duration - corrected, 3)
    if abs(delta) >= 0.001:
        shots[-1]["duration_seconds"] = round(max(minimum, float(shots[-1].get("duration_seconds", minimum)) + delta), 3)


def _find_scene_pack(project_root: Path, scene_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int, int]:
    scenes_data = _read_manifest(project_root, "scenes.json")
    direction = _read_manifest(project_root, "visual_direction.json")
    timing = _read_manifest(project_root, "narration_timing.json")

    scenes = scenes_data.get("scenes", []) if isinstance(scenes_data.get("scenes"), list) else []
    visual_scenes = direction.get("scenes", []) if isinstance(direction.get("scenes"), list) else []
    segments = timing.get("segments", []) if isinstance(timing.get("segments"), list) else []

    scene_index = next((index for index, item in enumerate(scenes) if str(item.get("id", "")) == scene_id), -1)
    if scene_index < 0:
        raise EditorError("Scene not found.")
    visual_index = next((index for index, item in enumerate(visual_scenes) if str(item.get("scene_id", "")) == scene_id), -1)
    if visual_index < 0:
        raise EditorError("Visual timeline for this scene is missing.")

    scene = scenes[scene_index]
    visual = visual_scenes[visual_index]
    timing_segment = next((item for item in segments if str(item.get("scene_id", "")) == scene_id), None)
    return scenes_data, direction, timing, scene_index, visual_index


def _write_edit_manifests(project_root: Path, scenes_data: dict[str, Any], direction: dict[str, Any], timing: dict[str, Any]) -> None:
    write_json(_manifests(project_root) / "scenes.json", scenes_data)
    write_json(_manifests(project_root) / "visual_direction.json", direction)
    if timing:
        write_json(_manifests(project_root) / "narration_timing.json", timing)


def _collect_timeline(project_root: Path) -> dict[str, Any]:
    scenes_data = _read_manifest(project_root, "scenes.json")
    direction = _read_manifest(project_root, "visual_direction.json")
    subtitles = _read_manifest(project_root, "subtitles.json")
    style = _read_manifest(project_root, "visual_style_profile.json")
    media = _read_manifest(project_root, "media_sources.json")
    timing = _read_manifest(project_root, "narration_timing.json")

    scenes = scenes_data.get("scenes", []) if isinstance(scenes_data.get("scenes"), list) else []
    direction_scenes = direction.get("scenes", []) if isinstance(direction.get("scenes"), list) else []
    assets = media.get("assets", []) if isinstance(media.get("assets"), list) else []
    segments = timing.get("segments", []) if isinstance(timing.get("segments"), list) else []
    segment_by_scene = {str(item.get("scene_id", "")): item for item in segments if isinstance(item, dict)}

    scene_rows: list[dict[str, Any]] = []
    absolute_start = 0.0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("id", ""))
        visual = next((item for item in direction_scenes if str(item.get("scene_id", "")) == scene_id), {})
        shots = visual.get("shots", []) if isinstance(visual.get("shots"), list) else []
        duration = _scene_duration(scene, segment_by_scene.get(scene_id))
        local_start = 0.0
        shot_rows = []
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_duration = float(shot.get("duration_seconds", 0.0) or 0.0)
            shot_end = local_start + shot_duration
            asset = shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}
            shot_rows.append(
                {
                    "id": str(shot.get("id", "")),
                    "start_seconds": round(local_start, 3),
                    "end_seconds": round(shot_end, 3),
                    "duration_seconds": round(shot_duration, 3),
                    "motion": str(shot.get("motion", "")),
                    "transition": str(visual.get("transition_to_next", "hard_cut")),
                    "media_type": str(asset.get("kind", "image")),
                    "media_title": str(asset.get("title") or asset.get("id") or asset.get("path") or "Asset"),
                    "media_id": str(asset.get("id", "")),
                    "license": str(asset.get("license", "")),
                    "source": str(asset.get("provider") or asset.get("source_url") or ""),
                    "scene_boundary": str(scene.get("heading", scene_id)),
                }
            )
            local_start = shot_end
        scene_rows.append(
            {
                "scene_id": scene_id,
                "heading": str(scene.get("heading", scene_id)),
                "narration": str(scene.get("narration", "")),
                "visual_purpose": str(scene.get("media_requirements", "")),
                "event": str(scene.get("event_shown") or ", ".join(str(item) for item in scene.get("events", []))),
                "queries": list(scene.get("archival_media_queries", []))[:8],
                "start_seconds": round(absolute_start, 3),
                "duration_seconds": round(duration, 3),
                "shots": shot_rows,
            }
        )
        absolute_start += duration

    subtitles_enabled = bool(style.get("subtitles", {}).get("enabled", False)) if isinstance(style.get("subtitles"), dict) else False
    subtitle_entries = subtitles.get("entries", []) if isinstance(subtitles.get("entries"), list) else []
    subtitle_rows = [item for item in subtitle_entries if isinstance(item, dict)]

    return {
        "scenes": scene_rows,
        "subtitles_enabled": subtitles_enabled,
        "subtitle_entries": subtitle_rows,
        "subtitle_style": style.get("subtitles", {}),
        "asset_count": len([item for item in assets if isinstance(item, dict)]),
    }


def create_revision(project_root: Path, *, label: str, operation_type: str, operation_summary: str, duration_delta_seconds: float = 0.0) -> dict[str, Any]:
    history = ensure_editor_workspace(project_root)
    revisions = history.get("revisions", []) if isinstance(history.get("revisions"), list) else []
    current_index = int(history.get("current_index", 0) or 0)
    if current_index < len(revisions) - 1:
        revisions = revisions[: current_index + 1]
    revision_id = f"rev{len(revisions):03}"
    _create_snapshot(project_root, revision_id)
    revision = {
        "id": revision_id,
        "label": compact_whitespace(label) or f"Revision {len(revisions)}",
        "created_at": _now(),
        "operation_type": operation_type,
        "operation_summary": operation_summary,
        "snapshot_dir": f"{REVISION_DIR}/{revision_id}",
        "duration_delta_seconds": round(float(duration_delta_seconds), 3),
    }
    revisions.append(revision)
    history["revisions"] = revisions
    history["current_index"] = len(revisions) - 1
    _save_history(project_root, history)
    return revision


def undo(project_root: Path) -> bool:
    history = ensure_editor_workspace(project_root)
    index = int(history.get("current_index", 0) or 0)
    if index <= 0:
        return False
    target = history["revisions"][index - 1]
    _restore_snapshot(project_root, str(target.get("id", "")))
    history["current_index"] = index - 1
    _save_history(project_root, history)
    return True


def redo(project_root: Path) -> bool:
    history = ensure_editor_workspace(project_root)
    revisions = history.get("revisions", []) if isinstance(history.get("revisions"), list) else []
    index = int(history.get("current_index", 0) or 0)
    if index >= len(revisions) - 1:
        return False
    target = revisions[index + 1]
    _restore_snapshot(project_root, str(target.get("id", "")))
    history["current_index"] = index + 1
    _save_history(project_root, history)
    return True


def restore_revision(project_root: Path, revision_id: str) -> bool:
    history = ensure_editor_workspace(project_root)
    revisions = history.get("revisions", []) if isinstance(history.get("revisions"), list) else []
    for index, revision in enumerate(revisions):
        if str(revision.get("id", "")) == revision_id:
            _restore_snapshot(project_root, revision_id)
            history["current_index"] = index
            _save_history(project_root, history)
            return True
    return False


def _find_shot(shots: list[dict[str, Any]], shot_id: str) -> tuple[int, dict[str, Any]]:
    for index, shot in enumerate(shots):
        if str(shot.get("id", "")) == shot_id:
            return index, shot
    raise EditorError("Shot not found.")


def _scene_target_duration(scene: dict[str, Any], timing: dict[str, Any], scene_id: str) -> float:
    segments = timing.get("segments", []) if isinstance(timing.get("segments"), list) else []
    segment = next((item for item in segments if str(item.get("scene_id", "")) == scene_id), None)
    return _scene_duration(scene, segment if isinstance(segment, dict) else None)


def apply_operation(project_root: Path, operation: dict[str, Any]) -> dict[str, Any]:
    ensure_editor_workspace(project_root)
    op_type = str(operation.get("type", "")).strip()
    scene_id = str(operation.get("scene_id", "")).strip()
    shot_id = str(operation.get("shot_id", "")).strip()

    if op_type in {
        "replace_asset",
        "remove_shot",
        "duplicate_shot",
        "move_shot",
        "set_shot_duration",
        "split_shot",
        "set_motion",
        "set_transition",
        "set_crop_mode",
        "set_static",
        "add_text",
        "add_overlay",
        "set_composition",
    }:
        scenes_data, direction, timing, scene_index, visual_index = _find_scene_pack(project_root, scene_id)
        scenes = scenes_data.get("scenes", [])
        scene = scenes[scene_index]
        visual_scenes = direction.get("scenes", [])
        visual = visual_scenes[visual_index]
        shots = visual.get("shots", []) if isinstance(visual.get("shots"), list) else []
        if not shots:
            raise EditorError("No shots found for this scene.")
        target_duration = _scene_target_duration(scene, timing, scene_id)

        if op_type == "replace_asset":
            media = _read_manifest(project_root, "media_sources.json")
            assets = media.get("assets", []) if isinstance(media.get("assets"), list) else []
            asset_id = str(operation.get("asset_id", ""))
            selected = next((item for item in assets if isinstance(item, dict) and str(item.get("id", "")) == asset_id), None)
            if not isinstance(selected, dict):
                raise EditorError("Selected media asset was not found.")
            shot_index, shot = _find_shot(shots, shot_id)
            shot["asset"] = {
                **(shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}),
                "id": str(selected.get("id", "")),
                "kind": str(selected.get("type", selected.get("kind", "image"))),
                "path": str(selected.get("path", "")),
                "source_url": str(selected.get("source_url", "")),
                "license": str(selected.get("license", selected.get("license_notes", ""))),
                "rights_status": str(selected.get("rights_status", "approved")),
                "title": str(selected.get("title", selected.get("id", ""))),
                "provider": str(selected.get("provider", selected.get("source", ""))),
            }
            shots[shot_index] = shot
        elif op_type == "remove_shot":
            if len(shots) <= 1:
                raise EditorError("A scene must contain at least one shot.")
            shot_index, _ = _find_shot(shots, shot_id)
            shots.pop(shot_index)
        elif op_type == "duplicate_shot":
            shot_index, shot = _find_shot(shots, shot_id)
            duplicate = copy.deepcopy(shot)
            duplicate["id"] = f"{shot_id}-dup-{len(shots) + 1}"
            shots.insert(shot_index + 1, duplicate)
        elif op_type == "move_shot":
            direction_value = str(operation.get("direction", "later"))
            shot_index, shot = _find_shot(shots, shot_id)
            if direction_value == "earlier" and shot_index > 0:
                shots[shot_index], shots[shot_index - 1] = shots[shot_index - 1], shots[shot_index]
            elif direction_value == "later" and shot_index < len(shots) - 1:
                shots[shot_index], shots[shot_index + 1] = shots[shot_index + 1], shots[shot_index]
        elif op_type == "set_shot_duration":
            seconds = float(operation.get("duration_seconds", 0.0) or 0.0)
            if seconds <= 0:
                raise EditorError("Duration must be greater than zero.")
            shot_index, shot = _find_shot(shots, shot_id)
            shot["duration_seconds"] = seconds
            shots[shot_index] = shot
        elif op_type == "split_shot":
            split_at = float(operation.get("split_seconds", 0.0) or 0.0)
            shot_index, shot = _find_shot(shots, shot_id)
            original = float(shot.get("duration_seconds", 0.0) or 0.0)
            if split_at <= 0.7 or split_at >= max(1.4, original - 0.7):
                raise EditorError("Split point is outside a valid range.")
            left = copy.deepcopy(shot)
            right = copy.deepcopy(shot)
            left["id"] = f"{shot_id}-a"
            right["id"] = f"{shot_id}-b"
            left["duration_seconds"] = round(split_at, 3)
            right["duration_seconds"] = round(original - split_at, 3)
            shots[shot_index : shot_index + 1] = [left, right]
        elif op_type == "set_motion":
            motion = MOTION_MAP.get(str(operation.get("motion", "")).strip().lower(), "")
            if not motion:
                raise EditorError("Unsupported motion style.")
            shot_index, shot = _find_shot(shots, shot_id)
            shot["motion"] = motion
            shots[shot_index] = shot
        elif op_type == "set_static":
            shot_index, shot = _find_shot(shots, shot_id)
            shot["motion"] = "static"
            shots[shot_index] = shot
        elif op_type == "set_transition":
            transition = compact_whitespace(str(operation.get("transition", "hard_cut")))
            visual["transition_to_next"] = transition or "hard_cut"
        elif op_type == "set_crop_mode":
            crop = compact_whitespace(str(operation.get("crop", "cover_16_9")))
            shot_index, shot = _find_shot(shots, shot_id)
            shot["crop"] = crop
            shots[shot_index] = shot
        elif op_type in {"add_text", "add_overlay"}:
            text = compact_whitespace(str(operation.get("text", "")))
            shot_index, shot = _find_shot(shots, shot_id)
            shot["text_overlay"] = text[:120]
            shots[shot_index] = shot
        elif op_type == "set_composition":
            composition = compact_whitespace(str(operation.get("composition", "single_frame")))
            shot_index, shot = _find_shot(shots, shot_id)
            shot["composition"] = composition or "single_frame"
            shots[shot_index] = shot

        _normalize_shot_durations(shots, target_duration)
        visual["shots"] = shots
        visual_scenes[visual_index] = visual
        direction["scenes"] = visual_scenes
        _write_edit_manifests(project_root, scenes_data, direction, timing)
        return {"ok": True, "duration_delta_seconds": 0.0}

    if op_type in {"disable_subtitles", "enable_subtitles", "set_subtitle_style", "edit_subtitle", "remove_subtitle"}:
        style = _read_manifest(project_root, "visual_style_profile.json")
        subtitle_style = style.get("subtitles", {}) if isinstance(style.get("subtitles"), dict) else {}
        subtitles = _read_manifest(project_root, "subtitles.json")
        entries = subtitles.get("entries", []) if isinstance(subtitles.get("entries"), list) else []

        if op_type == "disable_subtitles":
            subtitle_style["enabled"] = False
        elif op_type == "enable_subtitles":
            subtitle_style["enabled"] = True
        elif op_type == "set_subtitle_style":
            size = int(operation.get("size", subtitle_style.get("size", 16)) or 16)
            placement = str(operation.get("placement", "bottom")).strip().lower()
            subtitle_style["size"] = max(12, min(30, size))
            subtitle_style["max_lines"] = 2
            subtitle_style["enabled"] = bool(operation.get("enabled", subtitle_style.get("enabled", False)))
            subtitle_style["bottom_margin"] = 54 if placement == "bottom" else 160
            subtitle_style["placement"] = placement
        elif op_type == "edit_subtitle":
            scene_id = str(operation.get("scene_id", ""))
            new_text = compact_whitespace(str(operation.get("text", "")))
            changed = False
            for entry in entries:
                if isinstance(entry, dict) and str(entry.get("scene_id", "")) == scene_id:
                    entry["text"] = new_text
                    changed = True
            if not changed:
                entries.append({"scene_id": scene_id, "start_seconds": 0.0, "end_seconds": 0.0, "text": new_text})
        elif op_type == "remove_subtitle":
            scene_id = str(operation.get("scene_id", ""))
            entries = [entry for entry in entries if not (isinstance(entry, dict) and str(entry.get("scene_id", "")) == scene_id)]

        style["subtitles"] = subtitle_style
        subtitles["entries"] = entries
        subtitles["manual_edit"] = True
        write_json(_manifests(project_root) / "visual_style_profile.json", style)
        write_json(_manifests(project_root) / "subtitles.json", subtitles)
        return {"ok": True, "duration_delta_seconds": 0.0}

    if op_type == "edit_narration":
        scenes_data = _read_manifest(project_root, "scenes.json")
        scenes = scenes_data.get("scenes", []) if isinstance(scenes_data.get("scenes"), list) else []
        scene_id = str(operation.get("scene_id", ""))
        new_text = compact_whitespace(str(operation.get("text", "")))
        if not new_text:
            raise EditorError("Narration text cannot be empty.")
        changed = False
        for scene in scenes:
            if isinstance(scene, dict) and str(scene.get("id", "")) == scene_id:
                scene["narration"] = new_text
                changed = True
                break
        if not changed:
            raise EditorError("Scene not found.")
        scenes_data["scenes"] = scenes
        write_json(_manifests(project_root) / "scenes.json", scenes_data)
        return {"ok": True, "duration_delta_seconds": 0.0, "voiceover_regeneration_required": True}

    raise EditorError("Unsupported edit operation.")


def _extract_seconds_token(text: str) -> float:
    match = re.search(r"(\d+)\s*sec", text)
    if match:
        return float(match.group(1))
    return 0.0


def build_ai_edit_plan(project_root: Path, instruction: str, *, mode: str, selected_scene_id: str = "", selected_shot_id: str = "") -> dict[str, Any]:
    ensure_editor_workspace(project_root)
    text = compact_whitespace(instruction)
    lowered = text.lower()
    operations: list[dict[str, Any]] = []
    affected_scenes: list[str] = []
    needs_media_search = False
    voiceover_regeneration_required = False
    duration_delta_seconds = 0.0

    if "subtitle" in lowered and any(term in lowered for term in ("remove", "disable", "off")):
        operations.append({"type": "disable_subtitles" if mode == "documentary" else "remove_subtitle", "scene_id": selected_scene_id})
        if selected_scene_id:
            affected_scenes.append(selected_scene_id)

    if "subtitle" in lowered and any(term in lowered for term in ("smaller", "smaller.", "size")):
        operations.append({"type": "set_subtitle_style", "size": 14, "placement": "bottom", "enabled": True})

    if any(term in lowered for term in ("static", "keep this image static", "disable motion")) and selected_scene_id and selected_shot_id:
        operations.append({"type": "set_static", "scene_id": selected_scene_id, "shot_id": selected_shot_id})
        affected_scenes.append(selected_scene_id)

    if any(term in lowered for term in ("slow zoom", "push-in", "parallax", "pan")) and selected_scene_id and selected_shot_id:
        motion = "slow_zoom_in" if "slow zoom" in lowered else "controlled_push_in" if "push-in" in lowered else "parallax"
        operations.append({"type": "set_motion", "scene_id": selected_scene_id, "shot_id": selected_shot_id, "motion": motion})
        affected_scenes.append(selected_scene_id)

    if any(term in lowered for term in ("replace", "newspaper", "map", "archive video", "fewer generic")):
        needs_media_search = True
        if selected_scene_id and selected_shot_id:
            operations.append({"type": "replace_asset", "scene_id": selected_scene_id, "shot_id": selected_shot_id, "asset_id": ""})
            affected_scenes.append(selected_scene_id)

    if "shorten" in lowered or "faster" in lowered:
        seconds = _extract_seconds_token(lowered)
        if seconds <= 0:
            seconds = 5.0
        duration_delta_seconds = -seconds
        if selected_scene_id and selected_shot_id:
            operations.append({"type": "set_shot_duration", "scene_id": selected_scene_id, "shot_id": selected_shot_id, "duration_seconds": 2.0})
            affected_scenes.append(selected_scene_id)

    if any(term in lowered for term in ("narration", "voice-over", "voiceover")):
        voiceover_regeneration_required = True

    if not operations and selected_scene_id and selected_shot_id:
        operations.append({"type": "set_motion", "scene_id": selected_scene_id, "shot_id": selected_shot_id, "motion": "static"})
        affected_scenes.append(selected_scene_id)

    summary = {
        "what_will_change": "Edits will update the selected timeline and subtitle settings.",
        "scenes": list(dict.fromkeys(affected_scenes)),
        "estimated_duration_change_seconds": duration_delta_seconds,
        "needs_media_search": needs_media_search,
        "voiceover_regeneration_required": voiceover_regeneration_required,
    }
    plan = {
        "version": 1,
        "created_at": _now(),
        "instruction": text,
        "mode": mode,
        "selected_scene_id": selected_scene_id,
        "selected_shot_id": selected_shot_id,
        "operations": operations,
        "summary": summary,
        "status": "awaiting_confirmation",
    }
    write_json(_pending_plan_path(project_root), plan)
    return plan


def get_pending_plan(project_root: Path) -> dict[str, Any]:
    path = _pending_plan_path(project_root)
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def clear_pending_plan(project_root: Path) -> None:
    path = _pending_plan_path(project_root)
    path.unlink(missing_ok=True)


def apply_plan(project_root: Path) -> dict[str, Any]:
    plan = get_pending_plan(project_root)
    if not plan or plan.get("status") != "awaiting_confirmation":
        raise EditorError("No pending AI edit plan.")
    operations = plan.get("operations", []) if isinstance(plan.get("operations"), list) else []
    for operation in operations:
        if isinstance(operation, dict):
            apply_operation(project_root, operation)
    create_revision(
        project_root,
        label=f"AI edit - {str(plan.get('instruction', 'instruction'))[:42]}",
        operation_type="ai_plan",
        operation_summary="Applied AI instruction plan.",
        duration_delta_seconds=float(plan.get("summary", {}).get("estimated_duration_change_seconds", 0.0) or 0.0),
    )
    plan["status"] = "applied"
    write_json(_pending_plan_path(project_root), plan)
    return plan


def editor_state(project_root: Path) -> dict[str, Any]:
    history = ensure_editor_workspace(project_root)
    timeline = _collect_timeline(project_root)
    pending = get_pending_plan(project_root)
    revisions = history.get("revisions", []) if isinstance(history.get("revisions"), list) else []
    current_index = int(history.get("current_index", 0) or 0)
    return {
        "timeline": timeline,
        "pending_plan": pending,
        "revisions": revisions,
        "current_revision": revisions[current_index] if revisions and 0 <= current_index < len(revisions) else {},
        "can_undo": current_index > 0,
        "can_redo": current_index < max(0, len(revisions) - 1),
    }
