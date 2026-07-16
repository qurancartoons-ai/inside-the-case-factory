from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

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
            position = index / max(1, len(plan["scenes"]) - 1)
            producer_section = producer_sections.get(str(scene.get("scene_id")), {})
            role = str(producer_section.get("role") or ("hook" if index == 0 else "outro" if index == len(plan["scenes"]) - 1 else "climax" if position >= .65 else "development"))
            scene["story_role"] = role
            scene["pacing"] = str(producer_section.get("visual_rhythm") or ("urgent" if role in {"hook", "climax"} else "resolved" if role in {"outro", "closing"} else "measured"))
            scene["producer_ratios"] = producer_section.get("ratios", {})
            scene["effect_policy"] = "restrained"
            scene["asset_strategy"] = sorted({str(shot["asset"].get("kind", "archive")) for shot in scene["shots"]})
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
        lessons = _approved_lessons(project_root)
        director_report = {
            "version": 1, "render_number": render_number, "status": "ready", "producer_blueprint": "manifests/producer_blueprint.json" if producer else None,
            "story_arc": [s["story_role"] for s in plan["scenes"]],
            "chapter_pacing": [{"scene_id": s["scene_id"], "role": s["story_role"], "pacing": s["pacing"], "intensity": s["emotional_intensity"]} for s in plan["scenes"]],
            "shot_count": report["shot_count"], "improvements": improvements,
            "approved_lessons_applied": [str(item.get("id", "")) for item in lessons],
            "decision_summary": "Effects are restrained; clean frames are deliberately preserved between motivated camera moves.",
            "provider_selection": self._provider_selection(provider_router, "director_plan"),
        }
        plan["director"] = director_report
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
        attempts.append({**decision, "weak_categories": report.get("weak_categories", [])})
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
