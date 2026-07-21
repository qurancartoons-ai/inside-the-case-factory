from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from inside_case_factory.core.relevance import validate_scene_asset_gate
from inside_case_factory.core.visual_direction import build_cinematic_plan, validate_cinematic_plan
from inside_case_factory.utils.files import read_json, write_json


CRITIC_CATEGORIES = (
    "documentary_feel", "tension_arc", "pacing", "shot_variation", "visual_quality",
    "transitions", "audio", "subtitles", "narrative_clarity", "cinematic_quality",
    "monotony", "repetition", "ai_artificiality",
)


@dataclass(frozen=True)
class QualityPolicy:
    threshold: float = 80.0
    max_renders: int = 2
    max_rerender_cost_usd: float = 0.0
    estimated_rerender_cost_usd: float = 0.0

    @classmethod
    def from_pipeline(cls, pipeline: dict[str, Any]) -> "QualityPolicy":
        return cls(
            threshold=max(0.0, min(100.0, float(pipeline.get("director_quality_threshold", 80.0)))),
            max_renders=max(1, int(pipeline.get("director_max_renders", 2))),
            max_rerender_cost_usd=max(0.0, float(pipeline.get("director_rerender_budget_usd", 0.0))),
            estimated_rerender_cost_usd=max(0.0, float(pipeline.get("director_estimated_rerender_cost_usd", 0.0))),
        )


def _approved_lessons(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / "manifests" / "critic_feedback.json"
    if not path.exists():
        return []
    feedback = read_json(path)
    entries = feedback.get("entries", []) if isinstance(feedback, dict) else []
    return [item for item in entries if isinstance(item, dict) and item.get("approval_status") == "approved"]


def _asset_identity(asset: dict[str, Any]) -> str:
    if not isinstance(asset, dict):
        return ""
    for key in ("sha256", "source_url", "id"):
        value = str(asset.get(key, "")).strip()
        if value:
            return value.casefold()
    text = " ".join(str(asset.get(key, "")) for key in ("title", "description", "summary")).casefold()
    return " ".join(re.findall(r"[a-z0-9]{3,}", text))[:120]


def _source_signature(asset: dict[str, Any]) -> str:
    if not isinstance(asset, dict):
        return ""
    origin = str(asset.get("discovery", {}).get("source", "")).strip().casefold()
    if origin:
        return origin
    source_url = str(asset.get("source_url", "")).strip()
    if source_url:
        return (urlparse(source_url).netloc or source_url).casefold()
    return str(asset.get("provider", "")).strip().casefold()


def _location_signature(shot: dict[str, Any]) -> str:
    intent = shot.get("media_intent", {}) if isinstance(shot, dict) else {}
    locations = [str(item).strip().casefold() for item in intent.get("locations", []) if str(item).strip()]
    return "|".join(sorted(set(locations)))


def _is_still(asset: dict[str, Any]) -> bool:
    media_type = str(asset.get("type") or asset.get("media_type") or "").casefold()
    kind = str(asset.get("kind", "")).casefold()
    path = str(asset.get("path", "")).casefold()
    category = str(asset.get("source_category", "")).casefold()
    return (
        media_type in {"image", "photo", "still"}
        or kind in {"photo", "still", "document", "image"}
        or category in {"historical_photographs", "documentary_stills"}
        or any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
    )


def _quality_score(asset: dict[str, Any]) -> float:
    semantic = float(asset.get("semantic_match_score", 0.0) or 0.0)
    relevance = float(asset.get("relevance_score", 0.0) or 0.0)
    source = float(asset.get("source_policy_score", 0.0) or 0.0)
    return round(0.5 * semantic + 0.3 * relevance + 0.2 * source, 4)


def _is_semantically_safe(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    baseline_semantic = float(baseline.get("semantic_match_score", 0.0) or 0.0)
    baseline_relevance = float(baseline.get("relevance_score", 0.0) or 0.0)
    candidate_semantic = float(candidate.get("semantic_match_score", 0.0) or 0.0)
    candidate_relevance = float(candidate.get("relevance_score", 0.0) or 0.0)
    if bool(baseline.get("scene_match_passed", False)) and not bool(candidate.get("scene_match_passed", False)):
        return False
    if candidate_semantic + 0.08 < baseline_semantic:
        return False
    if candidate_relevance + 0.08 < baseline_relevance:
        return False
    if bool(baseline.get("archival_priority", False)) and not bool(candidate.get("archival_priority", False)):
        return False
    return True


def _diversity_gain(candidate: dict[str, Any], previous_asset: dict[str, Any] | None, previous_shot: dict[str, Any] | None, candidate_motion: str, candidate_composition: str) -> float:
    gain = 0.0
    if not isinstance(candidate, dict):
        return gain
    if isinstance(previous_asset, dict):
        if _asset_identity(candidate) != _asset_identity(previous_asset):
            gain += 1.6
        else:
            gain -= 1.6
        if _source_signature(candidate) != _source_signature(previous_asset):
            gain += 0.7
        else:
            gain -= 0.7
        if _is_still(candidate) and _is_still(previous_asset):
            gain -= 0.9
    if isinstance(previous_shot, dict):
        if candidate_motion != str(previous_shot.get("motion", "")):
            gain += 0.45
        else:
            gain -= 0.45
        if candidate_composition != str(previous_shot.get("composition", "")):
            gain += 0.35
        else:
            gain -= 0.35
    return gain


def _transition_reason(prev_scene: dict[str, Any] | None, scene: dict[str, Any]) -> str:
    if not isinstance(prev_scene, dict):
        return "Opening beat starts the visual grammar with a clean transition."
    prev_roles = str(prev_scene.get("story_role", ""))
    role = str(scene.get("story_role", ""))
    if prev_roles != role:
        return "Transition marks a narrative-role shift to keep pacing readable."
    prev_intensity = int(prev_scene.get("emotional_intensity", 0) or 0)
    intensity = int(scene.get("emotional_intensity", 0) or 0)
    if intensity > prev_intensity:
        return "Transition escalates tension between scenes."
    if intensity < prev_intensity:
        return "Transition decompresses after a higher-intensity beat."
    return "Transition preserves flow while avoiding repeated cadence."


def _apply_montage_diversity(plan: dict[str, Any], media_assets: list[dict[str, Any]]) -> dict[str, Any]:
    asset_lookup = {str(item.get("id", "")): item for item in media_assets if isinstance(item, dict) and str(item.get("id", ""))}
    candidate_by_shot: dict[str, list[dict[str, Any]]] = {}
    for item in media_assets:
        if not isinstance(item, dict):
            continue
        for shot_id in {str(value) for value in item.get("shot_ids", []) if str(value).strip()}:
            candidate_by_shot.setdefault(shot_id, []).append(item)

    total_shots = 0
    repeated_assets = 0
    repeated_sources = 0
    repeated_composition = 0
    static_count = 0
    still_count = 0
    long_uninterrupted_broll_penalty = 0.0
    no_visual_change_penalty = 0.0
    previous_asset: dict[str, Any] | None = None
    previous_shot: dict[str, Any] | None = None
    broll_streak = 0
    static_streak = 0

    for scene in plan.get("scenes", []):
        shots = scene.get("shots", []) if isinstance(scene.get("shots"), list) else []
        for shot in shots:
            shot_id = str(shot.get("id", ""))
            current_asset = shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}
            baseline = asset_lookup.get(str(current_asset.get("id", "")), current_asset)
            alternatives = [item for item in candidate_by_shot.get(shot_id, []) if isinstance(item, dict)]
            selected = baseline
            if alternatives:
                scored: list[tuple[float, dict[str, Any], float]] = []
                for candidate in alternatives:
                    if not _is_semantically_safe(candidate, baseline):
                        continue
                    candidate_quality = _quality_score(candidate)
                    if candidate_quality < 0.55 or float(candidate.get("semantic_match_score", 0.0) or 0.0) < 0.55:
                        continue
                    diversity = _diversity_gain(
                        candidate,
                        previous_asset,
                        previous_shot,
                        str(shot.get("motion", "")),
                        str(shot.get("composition", "single_frame")),
                    )
                    score = candidate_quality + 0.05 * diversity
                    scored.append((score, candidate, candidate_quality))
                if scored:
                    archival_scored = [item for item in scored if bool(item[1].get("archival_priority", False))]
                    if archival_scored:
                        scored = archival_scored
                    scored.sort(key=lambda pair: pair[0], reverse=True)
                    best = scored[0][1]
                    baseline_quality = _quality_score(baseline)
                    best_quality = _quality_score(best)
                    if best_quality + 0.03 >= baseline_quality:
                        selected = best
            if isinstance(selected, dict) and selected:
                shot["asset"] = selected

            selected_asset = shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}
            total_shots += 1
            motion = str(shot.get("motion", ""))
            composition = str(shot.get("composition", "single_frame"))
            shot_type = str(shot.get("shot_type", ""))
            is_static = motion in {"", "static"} or not bool(shot.get("apply_effect", True))
            is_still = _is_still(selected_asset)

            if is_static:
                static_count += 1
                static_streak += 1
            else:
                static_streak = 0
            if static_streak >= 3:
                no_visual_change_penalty += 0.3

            if is_still:
                still_count += 1

            if shot_type in {"b_roll", "supporting_detail"}:
                broll_streak += 1
            else:
                broll_streak = 0
            if broll_streak >= 3:
                long_uninterrupted_broll_penalty += 0.25

            if isinstance(previous_asset, dict):
                if _asset_identity(selected_asset) == _asset_identity(previous_asset):
                    repeated_assets += 1
                if _source_signature(selected_asset) and _source_signature(selected_asset) == _source_signature(previous_asset):
                    repeated_sources += 1
            if isinstance(previous_shot, dict) and composition == str(previous_shot.get("composition", "single_frame")):
                repeated_composition += 1

            shot["diversity_metadata"] = {
                "is_still": is_still,
                "is_static": is_static,
                "source_signature": _source_signature(selected_asset),
                "location_signature": _location_signature(shot),
                "semantic_guard": bool(selected_asset.get("scene_match_passed", False)) or float(selected_asset.get("semantic_match_score", 0.0) or 0.0) >= 0.55,
            }

            previous_asset = selected_asset
            previous_shot = shot

    location_signatures = []
    for scene in plan.get("scenes", []):
        for shot in scene.get("shots", []):
            signature = _location_signature(shot)
            if signature:
                location_signatures.append(signature)
    repeated_locations = max(0, len(location_signatures) - len(set(location_signatures)))

    repetition_penalty = round(min(1.0, (repeated_assets * 0.18 + repeated_sources * 0.11 + repeated_composition * 0.07 + repeated_locations * 0.05 + long_uninterrupted_broll_penalty)), 2)
    static_penalty = round(min(1.0, (still_count * 0.07 + static_count * 0.08 + no_visual_change_penalty)), 2)
    source_diversity_score = round(max(0.0, 1.0 - min(1.0, repeated_sources / max(1, total_shots - 1))), 2)
    motion_balance_score = round(max(0.0, 1.0 - min(1.0, static_count / max(1, total_shots))), 2)
    visual_variety_score = round(max(0.0, min(1.0, 1.0 - 0.6 * repetition_penalty - 0.4 * static_penalty)), 2)

    previous_scene = None
    for scene in plan.get("scenes", []):
        scene["transition_reason"] = _transition_reason(previous_scene, scene)
        previous_scene = scene

    return {
        "visual_variety_score": visual_variety_score,
        "repetition_penalty": repetition_penalty,
        "static_penalty": static_penalty,
        "source_diversity_score": source_diversity_score,
        "motion_balance_score": motion_balance_score,
    }


class DirectorEngine:
    """Creates a complete, reproducible editorial plan before each render."""

    def plan(
        self, project_root: Path, scenes: list[dict[str, Any]], *, width: int, height: int,
        render_number: int = 1, criticism: dict[str, Any] | None = None,
        provider_router: Any | None = None,
    ) -> dict[str, Any]:
        plan = build_cinematic_plan(project_root, scenes)
        producer_path = project_root / "manifests" / "producer_blueprint.json"
        producer = read_json(producer_path) if producer_path.exists() else {}
        producer_sections = {str(item.get("id")): item for item in producer.get("sections", [])}
        critique = criticism or {}
        weak = set(critique.get("weak_categories", []))
        improvements: list[str] = []
        global_shot_index = 0
        for index, scene in enumerate(plan["scenes"]):
            source_scene = scenes[index] if index < len(scenes) else {}
            position = index / max(1, len(plan["scenes"]) - 1)
            producer_section = producer_sections.get(str(scene.get("scene_id")), {})
            role = str(producer_section.get("role") or ("hook" if index == 0 else "outro" if index == len(plan["scenes"]) - 1 else "climax" if position >= .65 else "development"))
            scene["story_role"] = role
            scene["pacing"] = str(producer_section.get("visual_rhythm") or ("urgent" if role in {"hook", "climax"} else "resolved" if role in {"outro", "closing"} else "measured"))
            scene["producer_ratios"] = producer_section.get("ratios", {})
            scene["effect_policy"] = "restrained"
            scene["asset_strategy"] = sorted({str(shot["asset"].get("kind", "archive")) for shot in scene["shots"]})
            scene["media_search_queries"] = [str(item) for item in source_scene.get("archival_media_queries", []) if str(item).strip()]
            scene["alternative_media_queries"] = [str(item) for item in source_scene.get("alternative_media_queries", []) if str(item).strip()]
            scene["asset_requirements"] = {
                "scene_id": str(source_scene.get("id", scene.get("scene_id", ""))),
                "required_count": 2,
                "subjects": [*map(str, source_scene.get("people", [])), *map(str, source_scene.get("locations", []))],
                "events": [str(item) for item in source_scene.get("events", [])],
                "content_reason": str(source_scene.get("media_requirements", "Relevant archival evidence for this scene.")),
                "rights_review_separate": True,
            }
            if role == "climax":
                scene["emotional_intensity"] = 5
            elif role == "outro":
                scene["emotional_intensity"] = 2
            for shot_index, shot in enumerate(scene["shots"]):
                global_shot_index += 1
                shot["apply_effect"] = global_shot_index % 4 != 0
                if not shot["apply_effect"]:
                    shot["effects"] = []
                    shot["narrative_reason"] = "Hold a clean frame to avoid visual overstatement."
        if render_number > 1:
            offset = render_number - 1
            revised_shot_index = 0
            for scene_index, scene in enumerate(plan["scenes"]):
                for shot in scene["shots"]:
                    if weak & {"shot_variation", "monotony", "repetition", "cinematic_quality"}:
                        motions = ("controlled_push_in", "slow_zoom_out", "ken_burns_pan", "parallax", "rack_focus", "slow_zoom_in")
                        shot["motion"] = motions[(offset + revised_shot_index) % len(motions)]
                    revised_shot_index += 1
                if weak & {"transitions", "repetition"}:
                    transitions = ("hard_cut", "match_cut", "cross_dissolve", "document_to_scene", "dip_to_black")
                    scene["transition_to_next"] = transitions[(offset + scene_index) % len(transitions)]
            improvements.append("Varied shot motion and transitions in response to the approved render critique.")
        report = validate_cinematic_plan(plan, width=width, height=height)
        if not report["valid"]:
            raise RuntimeError("Director plan rejected: " + "; ".join(report["errors"]))
        media_path = project_root / "manifests" / "media_sources.json"
        media_assets = read_json(media_path).get("assets", []) if media_path.exists() else []
        workflow_path = project_root / "manifests" / "workflow.json"
        workflow = read_json(workflow_path) if workflow_path.exists() else {}
        run_quality_mode = str(workflow.get("run_quality_mode", "sample_or_demo"))
        diversity = _apply_montage_diversity(plan, [item for item in media_assets if isinstance(item, dict)])
        asset_gate = validate_scene_asset_gate(
            project_root,
            plan["scenes"],
            media_assets=[item for item in media_assets if isinstance(item, dict)],
            run_quality_mode=run_quality_mode,
        )
        if not asset_gate["passed"]:
            blocked_report = {
                "version": 1,
                "render_number": render_number,
                "status": "blocked",
                "run_quality_mode": run_quality_mode,
                "asset_gate": asset_gate,
                "blocking_reason": asset_gate.get("blocking_reason", "Asset gate blocked director finalization."),
            }
            write_json(project_root / "manifests" / "director_report.json", blocked_report)
            raise RuntimeError("Asset gate blocked director finalization: " + asset_gate.get("blocking_reason", "No acceptable asset was found."))
        lessons = _approved_lessons(project_root)
        director_report = {
            "version": 1, "render_number": render_number, "status": "ready", "producer_blueprint": "manifests/producer_blueprint.json" if producer else None,
            "story_arc": [s["story_role"] for s in plan["scenes"]],
            "chapter_pacing": [{"scene_id": s["scene_id"], "role": s["story_role"], "pacing": s["pacing"], "intensity": s["emotional_intensity"]} for s in plan["scenes"]],
            "shot_count": report["shot_count"], "improvements": improvements,
            "approved_lessons_applied": [str(item.get("id", "")) for item in lessons],
            "decision_summary": "Effects are restrained; clean frames are deliberately preserved between motivated camera moves.",
            "provider_selection": self._provider_selection(provider_router, "director_plan"),
            "run_quality_mode": run_quality_mode,
            "asset_gate": asset_gate,
            "visual_variety": diversity,
        }
        plan["director"] = director_report
        plan["visual_variety"] = diversity
        selection_rows = []
        for planned_scene in plan["scenes"]:
            for shot in planned_scene["shots"]:
                shot_id = str(shot["id"])
                alternatives = [
                    {"id": item.get("id"), "type": item.get("type", "image"), "provider": item.get("discovery", {}).get("source", ""), "relevance_score": item.get("shot_relevance_score", item.get("relevance_score")), "rights_status": item.get("rights_status", item.get("copyright_status")), "review_status": item.get("review_status")}
                    for item in media_assets if isinstance(item, dict) and shot_id in {str(value) for value in item.get("shot_ids", [])} and str(item.get("id")) != str(shot["asset"].get("id"))
                ]
                selection_rows.append({
                    "scene_id": planned_scene["scene_id"], "shot_id": shot_id, "media_intent": shot["media_intent"],
                    "chosen_asset": shot["asset"], "secondary_asset": shot.get("secondary_asset"), "alternatives": alternatives,
                    "duration_seconds": shot["duration_seconds"], "motion": shot["motion"], "composition": shot["composition"],
                    "narrative_reason": shot["narrative_reason"],
                    "diversity_metadata": shot.get("diversity_metadata", {}),
                    "transition_reason": planned_scene.get("transition_reason", ""),
                })
        write_json(project_root / "manifests" / "shot_media_manifest.json", {"version": 1, "project_slug": project_root.name, "shots": selection_rows})
        write_json(project_root / "manifests" / "director_plan.json", plan)
        write_json(project_root / "manifests" / "director_report.json", director_report)
        write_json(project_root / "manifests" / "visual_direction.json", plan)
        write_json(project_root / "manifests" / "visual_quality_report.json", report)
        return plan

    @staticmethod
    def _provider_selection(router: Any | None, task: str) -> dict[str, str]:
        provider = router.choose("text", task) if router is not None else None
        return {"provider": provider.name if provider else "local_deterministic", "model": provider.config.model if provider else "built_in"}

    def review_revisions(self, project_root: Path, scenes: list[dict[str, Any]], scene_ids: list[str]) -> dict[str, Any]:
        scene_ids_set = set(scene_ids)
        results = []
        for scene in scenes:
            if str(scene.get("id")) not in scene_ids_set:
                continue
            results.append({
                "scene_id": str(scene.get("id")), "status": "ready",
                "camera_preference": scene.get("camera_preference"),
                "changed_components": sorted({component for directive in scene.get("revision_directives", []) for component in directive.get("components", []) if component in {"director", "edit", "media"}}),
            })
        report = {"version": 1, "engine": "director", "scene_ids": scene_ids, "results": results}
        write_json(project_root / "manifests" / "director_revision_review.json", report)
        return report


class CriticEngine:
    """Scores the finished edit from render evidence, without modifying approved content."""

    def analyze(self, project_root: Path, *, render_number: int, duration_seconds: float, provider_router: Any | None = None) -> dict[str, Any]:
        plan = read_json(project_root / "manifests" / "director_plan.json")
        scenes = plan.get("scenes", [])
        shots = [shot for scene in scenes for shot in scene.get("shots", [])]
        motions = [str(shot.get("motion", "")) for shot in shots]
        transitions = [str(scene.get("transition_to_next", "")) for scene in scenes]
        unique_motion_ratio = len(set(motions)) / max(1, len(motions))
        unique_transition_ratio = len(set(transitions)) / max(1, len(transitions))
        avg_shot = duration_seconds / max(1, len(shots))
        pacing = max(55.0, 100.0 - abs(avg_shot - 6.0) * 7.0)
        scores = {
            "documentary_feel": 88.0 if shots else 0.0,
            "tension_arc": 90.0 if {s.get("story_role") for s in scenes} >= {"hook", "climax"} and {s.get("story_role") for s in scenes} & {"outro", "closing"} else 65.0,
            "pacing": pacing,
            "shot_variation": min(96.0, 65.0 + unique_motion_ratio * 35.0),
            "visual_quality": 86.0,
            "transitions": min(95.0, 68.0 + unique_transition_ratio * 30.0),
            "audio": 90.0 if plan.get("sound_design", {}).get("voice_priority") else 55.0,
            "subtitles": 90.0 if (project_root / "manifests" / "subtitles.srt").exists() else 0.0,
            "narrative_clarity": 87.0 if all(scene.get("claim_ids") is not None for scene in scenes) else 68.0,
            "cinematic_quality": min(94.0, 72.0 + unique_motion_ratio * 24.0),
            "monotony": min(96.0, 64.0 + unique_motion_ratio * 36.0),
            "repetition": min(96.0, 64.0 + min(unique_motion_ratio, unique_transition_ratio) * 36.0),
            "ai_artificiality": 88.0 if any(not shot.get("apply_effect", True) for shot in shots) else 70.0,
        }
        scores = {key: round(max(0.0, min(100.0, scores[key])), 1) for key in CRITIC_CATEGORIES}
        overall = round(sum(scores.values()) / len(scores), 1)
        weak = [key for key, score in scores.items() if score < 80.0]
        criticisms = [f"{key.replace('_', ' ').title()} needs attention ({scores[key]:.1f}/100)." for key in weak]
        report = {
            "version": 1, "render_number": render_number, "analyzed_at": datetime.now(UTC).isoformat(),
            "scores": scores, "overall_score": overall, "weak_categories": weak,
            "main_criticisms": criticisms[:5], "evidence": {"duration_seconds": round(duration_seconds, 3), "shot_count": len(shots), "average_shot_seconds": round(avg_shot, 3)},
            "provider_selection": DirectorEngine._provider_selection(provider_router, "critic_review"),
        }
        write_json(project_root / "manifests" / f"critic_report_render_{render_number}.json", report)
        write_json(project_root / "manifests" / "critic_report.json", report)
        return report

    def review_revisions(self, project_root: Path, scenes: list[dict[str, Any]], scene_ids: list[str]) -> dict[str, Any]:
        selected = [scene for scene in scenes if str(scene.get("id")) in set(scene_ids)]
        results = []
        for scene in selected:
            directives = scene.get("revision_directives", [])
            results.append({
                "scene_id": str(scene.get("id")), "status": "pass" if directives else "needs_revision",
                "clarity_score": 88.0 if str(scene.get("narration", "")).strip() else 0.0,
                "scope": "changed_scene_only",
            })
        report = {"version": 1, "engine": "critic", "scene_ids": scene_ids, "results": results}
        write_json(project_root / "manifests" / "critic_revision_review.json", report)
        return report


def improvement_decision(report: dict[str, Any], policy: QualityPolicy, render_number: int) -> dict[str, Any]:
    score = float(report.get("overall_score", 0.0))
    projected_cost = policy.estimated_rerender_cost_usd * render_number
    budget_allows = projected_cost <= policy.max_rerender_cost_usd
    needs = score < policy.threshold
    allowed = needs and render_number < policy.max_renders and budget_allows
    if not needs:
        reason = "Quality threshold reached."
    elif render_number >= policy.max_renders:
        reason = "Render-count budget reached."
    elif not budget_allows:
        reason = "Rerender cost budget reached."
    else:
        reason = "Score is below threshold; a revised direction and edit plan is required."
    return {"rerender_required": allowed, "reason": reason, "score": score, "threshold": policy.threshold, "render_number": render_number, "projected_rerender_cost_usd": projected_cost}


def save_improvement_state(project_root: Path, decision: dict[str, Any], report: dict[str, Any]) -> None:
    path = project_root / "manifests" / "quality_cycle.json"
    previous = read_json(path) if path.exists() else {"version": 1, "attempts": []}
    attempts = previous.setdefault("attempts", [])
    if not any(item.get("render_number") == decision["render_number"] for item in attempts):
        attempts.append({
            **decision,
            "weak_categories": report.get("weak_categories", []),
            "overall_score": float(report.get("overall_score", 0.0)),
            "producer_score": float(report.get("producer_score", 0.0)) if report.get("producer_score") is not None else None,
        })
    previous.update({"status": "rerender_pending" if decision["rerender_required"] else "completed", "latest_decision": decision})
    write_json(path, previous)


def record_feedback(project_root: Path, criticisms: list[str]) -> None:
    """Queues lessons for explicit review; never approves or applies them automatically."""
    path = project_root / "manifests" / "critic_feedback.json"
    data = read_json(path) if path.exists() else {"version": 1, "entries": []}
    known = {str(item.get("text", "")) for item in data["entries"]}
    for text in criticisms:
        if text not in known:
            data["entries"].append({"id": f"feedback-{len(data['entries']) + 1}", "text": text, "approval_status": "pending_review"})
    write_json(path, data)
