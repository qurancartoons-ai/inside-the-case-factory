from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from inside_case_factory.utils.files import read_json, write_json


BLUEPRINT_ROLES = ("intro", "hook", "context", "escalation", "turning_point", "climax", "aftermath", "closing")
PRODUCER_CATEGORIES = (
    "story_rhythm", "tension_arc", "emotional_impact", "information_density",
    "viewer_retention", "variation", "professionalism",
)


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _scene_text(scene: dict[str, Any]) -> str:
    return str(scene.get("narration") or scene.get("text") or scene.get("heading") or "")


def _words(scene: dict[str, Any]) -> int:
    return len(re.findall(r"\w+", _scene_text(scene)))


def _role_for(index: int, count: int) -> str:
    if count <= 1:
        return "hook"
    if count == 2:
        return ("hook", "closing")[index]
    if count == 3:
        return ("hook", "climax", "closing")[index]
    if count == 4:
        return ("hook", "context", "climax", "closing")[index]
    position = index / max(1, count - 1)
    role_index = min(len(BLUEPRINT_ROLES) - 1, int(position * len(BLUEPRINT_ROLES)))
    return BLUEPRINT_ROLES[role_index]


class ProducerEngine:
    """Owns documentary-wide story rhythm; it never chooses individual shots."""

    def plan(
        self,
        project_root: Path,
        scenes: list[dict[str, Any]],
        *,
        render_number: int = 1,
        previous_review: dict[str, Any] | None = None,
        provider_router: Any | None = None,
    ) -> dict[str, Any]:
        total_duration = sum(max(1.0, float(scene.get("duration_seconds", 10.0))) for scene in scenes)
        previous_review = previous_review or {}
        weak = set(previous_review.get("weak_categories", []))
        sections: list[dict[str, Any]] = []
        emotional_arc: list[dict[str, Any]] = []
        interview_plan: list[dict[str, Any]] = []
        retention_curve: list[dict[str, Any]] = []
        previous_structure = ""
        for index, scene in enumerate(scenes):
            role = _role_for(index, len(scenes))
            position = index / max(1, len(scenes) - 1)
            duration = max(1.0, float(scene.get("duration_seconds", total_duration / max(1, len(scenes)))))
            density = _clamp((_words(scene) / duration) * 32.0)
            tension = _clamp(35 + position * 45 + (18 if role == "climax" else 8 if role in {"hook", "turning_point"} else 0))
            curiosity = _clamp(78 - position * 24 + (12 if role in {"hook", "turning_point"} else 0))
            emotion = _clamp(30 + position * 35 + (22 if role == "climax" else 0))
            impact = _clamp((tension + emotion) / 2)
            clarity = _clamp(92 - max(0.0, density - 75) * .7)
            has_interview = bool(scene.get("interview") or scene.get("speaker") or scene.get("reference_interviews"))
            interview_ratio = .45 if has_interview else (.18 if role in {"context", "turning_point", "aftermath"} else 0.0)
            voice_ratio = .78 if interview_ratio == 0 else .42
            if "information_density" in weak:
                voice_ratio = max(.35, voice_ratio - .12)
            broll_ratio = round(max(0.0, 1.0 - voice_ratio - interview_ratio), 2)
            visual_mode = (
                "fast" if role in {"hook", "climax"} else
                "accelerating" if role in {"escalation", "turning_point"} else
                "breathing_room" if role in {"aftermath", "closing"} else "measured"
            )
            structure = ("interview" if interview_ratio else "voice_over") + ":" + visual_mode
            if structure == previous_structure:
                visual_mode = "breathing_room" if visual_mode != "breathing_room" else "measured"
                structure = ("interview" if interview_ratio else "voice_over") + ":" + visual_mode
            previous_structure = structure
            section = {
                "id": str(scene.get("id", f"s{index + 1:02}")), "role": role,
                "purpose": {
                    "intro": "Establish place, time and promise without repeating a stock opening.",
                    "hook": "Create a precise unanswered question.", "context": "Supply only the facts needed to follow the case.",
                    "escalation": "Raise stakes through new sourced information.", "turning_point": "Reframe what the viewer thinks happened.",
                    "climax": "Deliver the strongest supported revelation late in the film.", "aftermath": "Let consequences and uncertainty register.",
                    "closing": "Resolve the central question as far as evidence permits and end decisively.",
                }[role],
                "desired_emotion": "urgency" if role in {"hook", "climax"} else "reflection" if role in {"aftermath", "closing"} else "focused curiosity",
                "estimated_duration_seconds": round(duration, 3), "information_density": density,
                "ratios": {"voice_over": round(voice_ratio, 2), "interview": round(interview_ratio, 2), "b_roll": broll_ratio},
                "visual_mix": {"maps_documents": role in {"context", "turning_point"}, "archive": role not in {"intro", "closing"}, "graphics": role in {"context", "escalation"}},
                "visual_rhythm": visual_mode, "scene_structure": structure,
            }
            sections.append(section)
            emotional_arc.append({"scene_id": section["id"], "tension": tension, "curiosity": curiosity, "emotion": emotion, "impact": impact, "clarity": clarity})
            retention = _clamp(100 - position * 22 - max(0.0, density - 80) * .35 + (7 if role in {"hook", "turning_point", "climax"} else 0))
            retention_curve.append({"scene_id": section["id"], "position": round(position, 3), "estimated_retention": retention})
            if interview_ratio:
                maximum = min(45.0, duration * interview_ratio)
                interview_plan.append({
                    "scene_id": section["id"], "start_after_seconds": round(min(8.0, duration * .2), 2),
                    "maximum_duration_seconds": round(maximum, 2), "voice_over_stops_before_seconds": .6,
                    "subtitles_from_interview_start": True, "b_roll_overlay": {"start_fraction": .35, "end_fraction": .72},
                    "return_to_speaker_fraction": .72, "preserve_context": True,
                })
        pacing_issues = self.detect_pacing(scenes, sections)
        attention = self.attention_analysis(sections, emotional_arc, retention_curve, pacing_issues)
        blueprint = {
            "version": 1, "render_number": render_number, "generated_at": datetime.now(UTC).isoformat(),
            "status": "ready", "total_duration_seconds": round(total_duration, 3), "sections": sections,
            "documentary_structure": [
                {"role": role, "estimated_duration_seconds": round(total_duration * weight, 3)}
                for role, weight in zip(BLUEPRINT_ROLES, (.06, .08, .18, .18, .14, .16, .12, .08), strict=True)
            ],
            "emotional_arc": emotional_arc, "interview_plan": interview_plan, "retention_curve": retention_curve,
            "pacing_analysis": pacing_issues, "attention_analysis": attention,
            "cinematic_rules": {"unique_scene_builds": True, "vary_intro": True, "climax_after_fraction": .65, "strong_outro": True},
            "revision_scope": sorted(weak),
            "provider_selection": self._provider_selection(provider_router, "producer_blueprint"),
        }
        write_json(project_root / "manifests" / "producer_blueprint.json", blueprint)
        return blueprint

    @staticmethod
    def _provider_selection(router: Any | None, task: str) -> dict[str, str]:
        provider = router.choose("text", task) if router is not None else None
        return {"provider": provider.name if provider else "local_deterministic", "model": provider.config.model if provider else "built_in"}

    def detect_pacing(self, scenes: list[dict[str, Any]], sections: list[dict[str, Any]]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        consecutive: list[str] = []
        seen_text: dict[str, str] = {}
        for scene, section in zip(scenes, sections, strict=True):
            scene_id = str(section["id"])
            duration = float(section["estimated_duration_seconds"])
            ratios = section["ratios"]
            mode = "interview" if ratios["interview"] >= .4 else "b_roll" if ratios["b_roll"] >= .5 else "voice_over"
            consecutive.append(mode)
            if duration > 90 and section["information_density"] > 65:
                issues.append({"scene_id": scene_id, "kind": "explanation_too_long"})
            if ratios["voice_over"] > .75:
                issues.append({"scene_id": scene_id, "kind": "too_much_voice_over"})
            normalized = " ".join(_scene_text(scene).lower().split())
            if normalized and normalized in seen_text:
                issues.append({"scene_id": scene_id, "kind": "repetition", "duplicates": seen_text[normalized]})
            seen_text[normalized] = scene_id
        for index in range(2, len(consecutive)):
            if len(set(consecutive[index - 2:index + 1])) == 1:
                issues.append({"scene_id": str(sections[index]["id"]), "kind": f"too_many_{consecutive[index]}_in_a_row"})
        return {"issues": issues, "issue_count": len(issues)}

    def attention_analysis(
        self, sections: list[dict[str, Any]], arc: list[dict[str, Any]], retention: list[dict[str, Any]], pacing: dict[str, Any]
    ) -> dict[str, Any]:
        risks: list[dict[str, Any]] = []
        for section, scores, point in zip(sections, arc, retention, strict=True):
            reasons = []
            if scores["tension"] < 42 and scores["curiosity"] < 55:
                reasons.append("low_energy")
            if section["information_density"] > 85:
                reasons.append("information_overload")
            if point["estimated_retention"] < 68:
                reasons.append("viewer_loss_risk")
            if reasons:
                risks.append({"scene_id": section["id"], "reasons": reasons})
        risks.extend({"scene_id": issue["scene_id"], "reasons": [issue["kind"]]} for issue in pacing["issues"])
        return {"risk_points": risks, "director_replan_recommended": bool(risks), "lowest_retention": min((p["estimated_retention"] for p in retention), default=0)}

    def review_render(self, project_root: Path, critic_report: dict[str, Any], *, provider_router: Any | None = None) -> dict[str, Any]:
        blueprint = read_json(project_root / "manifests" / "producer_blueprint.json")
        issues = blueprint.get("pacing_analysis", {}).get("issues", [])
        retention = blueprint.get("retention_curve", [])
        variation = len({s.get("scene_structure") for s in blueprint.get("sections", [])}) / max(1, len(blueprint.get("sections", [])))
        critic_score = float(critic_report.get("overall_score", 0))
        scores = {
            "story_rhythm": _clamp(92 - len(issues) * 7),
            "tension_arc": _clamp(critic_report.get("scores", {}).get("tension_arc", 75)),
            "emotional_impact": _clamp(sum(p.get("impact", 0) for p in blueprint.get("emotional_arc", [])) / max(1, len(blueprint.get("emotional_arc", []))) + 18),
            "information_density": _clamp(94 - sum(1 for s in blueprint.get("sections", []) if s.get("information_density", 0) > 85) * 12),
            "viewer_retention": _clamp(sum(p.get("estimated_retention", 0) for p in retention) / max(1, len(retention))),
            "variation": _clamp(62 + variation * 38), "professionalism": _clamp((critic_score + 90) / 2),
        }
        weak = [key for key, score in scores.items() if score < 80]
        overall = round(sum(scores.values()) / len(scores), 1)
        report = {
            "version": 1, "render_number": critic_report.get("render_number", 1), "scores": scores,
            "overall_score": overall, "weak_categories": weak,
            "improvement_plan": [f"Revise only {category.replace('_', ' ')}; preserve approved content and unaffected scenes." for category in weak],
            "director_replan_required": bool(weak), "critic_validation_score": critic_score,
            "provider_selection": self._provider_selection(provider_router, "producer_review"),
        }
        write_json(project_root / "manifests" / "producer_report.json", report)
        return report

    def review_revisions(self, project_root: Path, scenes: list[dict[str, Any]], scene_ids: list[str]) -> dict[str, Any]:
        selected = [scene for scene in scenes if str(scene.get("id")) in set(scene_ids)]
        results = [{
            "scene_id": str(scene.get("id")), "status": "pass",
            "reviewed_directives": scene.get("revision_directives", []),
            "scope": "changed_scene_only",
        } for scene in selected]
        report = {"version": 1, "engine": "producer", "scene_ids": scene_ids, "results": results}
        write_json(project_root / "manifests" / "producer_revision_review.json", report)
        return report
