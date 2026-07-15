from __future__ import annotations

from pathlib import Path
from typing import Any

from inside_case_factory.providers.visual_assets import resolve_scene_assets
from inside_case_factory.utils.files import read_json, write_json


MOTIONS = ("slow_zoom_in", "ken_burns_pan", "controlled_push_in", "parallax", "slow_zoom_out", "rack_focus")
TRANSITIONS = ("hard_cut", "cross_dissolve", "dip_to_black", "match_cut", "directional_wipe", "document_to_scene", "blur")


def default_visual_style_profile() -> dict[str, Any]:
    return {
        "version": 1,
        "color_temperature": "neutral_warm",
        "contrast": 1.06,
        "saturation": 0.88,
        "grain": 0.035,
        "vignette": 0.18,
        "depth_effect": "restrained",
        "typography": {"family": "DejaVu Sans", "title_weight": 700, "safe_margin_percent": 7},
        "subtitles": {"size": 26, "max_chars_per_line": 58, "outline": 2, "bottom_margin": 70},
        "archival_treatment": {"preserve_source_color": True, "max_grain": 0.06, "gentle_contrast_match": True},
    }


def _shot_type(scene: dict[str, Any]) -> str:
    if scene.get("locations"):
        return "map_establishing"
    if scene.get("dates"):
        return "timeline_evidence"
    if scene.get("people"):
        return "archival_character"
    return "document_detail"


def _sound_cues(scene: dict[str, Any], start: float, duration: float) -> list[dict[str, Any]]:
    events = " ".join(str(item).lower() for item in scene.get("events", []))
    locations = " ".join(str(item).lower() for item in scene.get("locations", []))
    cues = [{
        "kind": "room_tone", "start_seconds": start, "duration_seconds": duration, "gain_db": -30.0,
        "fade_in_seconds": 0.15, "fade_out_seconds": 0.2, "optional": True,
    }]
    if any(word in locations for word in ("street", "city", "station", "road")):
        cues.append({
            "kind": "city_ambience", "start_seconds": start, "duration_seconds": duration, "gain_db": -28.0,
            "fade_in_seconds": 0.2, "fade_out_seconds": 0.25, "optional": True,
        })
    if any(word in events for word in ("document", "letter", "report", "hearing")):
        cues.append({
            "kind": "paper", "start_seconds": start + min(1.0, duration / 4), "duration_seconds": 1.2,
            "gain_db": -24.0, "fade_in_seconds": 0.08, "fade_out_seconds": 0.12, "optional": True,
        })
    return cues


def build_cinematic_plan(project_root: Path, scenes: list[dict[str, Any]], style: dict[str, Any] | None = None) -> dict[str, Any]:
    style_profile = style or default_visual_style_profile()
    media = read_json(project_root / "manifests" / "media_sources.json")
    assets = media.get("assets", []) if isinstance(media, dict) else []
    plans = []
    used_assets: set[str] = set()
    previous_motion = ""
    previous_transition = ""
    sound_cues: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(scenes):
        scene_id = str(scene.get("id", f"s{scene_index + 1:02}"))
        duration = max(2.5, float(scene.get("duration_seconds", scene.get("estimated_duration_seconds", 8.0))))
        candidates = resolve_scene_assets(project_root, scene, assets)
        shot_count = max(1, min(5, int((duration + 5.9) // 6.0)))
        base_duration = duration / shot_count
        shots = []
        for shot_index in range(shot_count):
            available = [item for item in candidates if str(item["id"]) not in used_assets]
            reusable = [item for item in candidates if item.get("generated")]
            pool = available or reusable or candidates
            asset = pool[shot_index % len(pool)].copy()
            if str(asset["id"]) in used_assets and asset.get("generated"):
                asset["id"] = f"{asset['id']}-shot-{shot_index + 1}"
            used_assets.add(str(asset["id"]))
            motion = MOTIONS[(scene_index + shot_index) % len(MOTIONS)]
            if motion == previous_motion:
                motion = MOTIONS[(MOTIONS.index(motion) + 1) % len(MOTIONS)]
            previous_motion = motion
            shots.append({
                "id": f"{scene_id}-shot-{shot_index + 1}",
                "asset": asset,
                "shot_type": _shot_type(scene) if shot_index == 0 else "supporting_detail",
                "duration_seconds": round(base_duration, 3),
                "motion": motion,
                "crop": "cover_16_9",
                "framing": "rule_of_thirds" if shot_index % 2 == 0 else "center_detail",
                "focus_point": "primary_subject" if scene.get("people") else "evidence_detail",
                "text_overlay": "" if shot_index else str(scene.get("heading", ""))[:80],
                "document_highlight": bool(asset.get("kind") == "document"),
                "map_animation": "route_reveal" if asset.get("kind") == "map" else "none",
                "timeline_animation": "progressive_markers" if asset.get("kind") == "timeline" else "none",
                "composition": "split_screen" if shot_index == 1 and len(candidates) > 2 else (
                    "picture_in_picture" if asset.get("kind") == "document" and shot_index > 0 else "single_frame"
                ),
                "effects": ["vignette", "depth"] if motion in {"parallax", "rack_focus"} else ["vignette"],
                "narrative_reason": "Emphasize the approved claim without inventing visual facts.",
                "claim_ids": [str(item) for item in scene.get("claim_ids", [])],
            })
        transition = TRANSITIONS[scene_index % len(TRANSITIONS)]
        if transition == previous_transition:
            transition = "hard_cut"
        previous_transition = transition
        start = float(scene.get("start_seconds", 0.0))
        cues = _sound_cues(scene, start, duration)
        sound_cues.extend({**cue, "scene_id": scene_id} for cue in cues)
        plans.append({
            "scene_id": scene_id,
            "dominant_shot_type": _shot_type(scene),
            "supporting_asset_ids": [str(shot["asset"]["id"]) for shot in shots[1:]],
            "shots": shots,
            "transition_to_next": transition if scene_index < len(scenes) - 1 else "dip_to_black",
            "emotional_intensity": min(5, 2 + (1 if scene_index == 0 else 0) + (1 if scene.get("events") else 0)),
            "claim_ids": [str(item) for item in scene.get("claim_ids", [])],
            "source_links": sorted({str(shot["asset"].get("source_url", "")) for shot in shots if shot["asset"].get("source_url")}),
        })
    return {
        "version": 1,
        "provider_order": ["approved_archive", "approved_local_media", "evidence_graphics", "offline_safe_fallback"],
        "style_profile": style_profile,
        "scenes": plans,
        "sound_design": {"voice_priority": True, "ducking": True, "voice_target_lufs": -16.0, "effects_peak_db": -18.0, "cues": sound_cues},
    }


def validate_cinematic_plan(plan: dict[str, Any], *, width: int = 1920, height: int = 1080) -> dict[str, Any]:
    errors: list[str] = []
    scenes = plan.get("scenes", [])
    motions: list[str] = []
    transitions: list[str] = []
    asset_ids: list[str] = []
    for scene in scenes:
        shots = scene.get("shots", [])
        if not shots:
            errors.append(f"{scene.get('scene_id')}: no approved asset or safe fallback")
        for shot in shots:
            duration = float(shot.get("duration_seconds", 0))
            motion = str(shot.get("motion", ""))
            if duration > 9.0 and motion in {"", "static"}:
                errors.append(f"{shot.get('id')}: prolonged static shot")
            if duration <= 0:
                errors.append(f"{shot.get('id')}: invalid shot duration")
            if motion not in MOTIONS:
                errors.append(f"{shot.get('id')}: unsupported or unmotivated motion")
            motions.append(motion)
            asset = shot.get("asset", {})
            asset_ids.append(str(asset.get("id", "")))
            if str(asset.get("rights_status", "")) not in {"approved", "owned", "licensed", "public_domain"}:
                errors.append(f"{shot.get('id')}: media rights are not approved")
            if not asset.get("claim_ids") and scene.get("claim_ids"):
                errors.append(f"{shot.get('id')}: missing claim provenance")
            overlay = str(shot.get("text_overlay", ""))
            if len(overlay) > 80:
                errors.append(f"{shot.get('id')}: text overlay is unreadably long")
        transitions.append(str(scene.get("transition_to_next", "")))
    if width / max(1, height) < 1.7 or width / max(1, height) > 1.9:
        errors.append("render aspect ratio must be 16:9")
    if len(asset_ids) != len(set(asset_ids)):
        errors.append("the same asset is repeated across shots")
    if motions and max(motions.count(item) for item in set(motions)) > max(2, int(len(motions) * 0.5)):
        errors.append("one camera movement is overused")
    if transitions and max(transitions.count(item) for item in set(transitions)) > max(2, int(len(transitions) * 0.5)):
        errors.append("one transition is overused")
    sound = plan.get("sound_design", {})
    if not sound.get("voice_priority") or not sound.get("ducking"):
        errors.append("voice-over dominance and ducking are required")
    if float(sound.get("effects_peak_db", 0)) > -12.0:
        errors.append("sound effects can overpower voice-over")
    for cue in sound.get("cues", []):
        if float(cue.get("fade_in_seconds", 0)) <= 0 or float(cue.get("fade_out_seconds", 0)) <= 0:
            errors.append(f"{cue.get('scene_id', 'sound cue')}: abrupt audio transition")
    if not plan.get("style_profile"):
        errors.append("visual style profile is missing")
    return {"valid": not errors, "errors": errors, "scene_count": len(scenes), "shot_count": len(asset_ids)}


def write_cinematic_plan(project_root: Path, scenes: list[dict[str, Any]], *, width: int, height: int) -> dict[str, Any]:
    style_path = project_root / "manifests" / "visual_style_profile.json"
    style = read_json(style_path) if style_path.exists() else default_visual_style_profile()
    write_json(style_path, style)
    plan = build_cinematic_plan(project_root, scenes, style)
    report = validate_cinematic_plan(plan, width=width, height=height)
    write_json(project_root / "manifests" / "visual_direction.json", plan)
    write_json(project_root / "manifests" / "visual_quality_report.json", report)
    if not report["valid"]:
        raise RuntimeError("Cinematic plan rejected: " + "; ".join(report["errors"]))
    return plan
