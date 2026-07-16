from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.autonomous_direction import (
    CRITIC_CATEGORIES,
    CriticEngine,
    DirectorEngine,
    QualityPolicy,
    improvement_decision,
    record_feedback,
    save_improvement_state,
)
from inside_case_factory.core.project import create_project
from inside_case_factory.utils.files import read_json, write_json


def scenes() -> list[dict[str, object]]:
    return [
        {"id": f"s{i:02}", "heading": f"Chapter {i}", "duration_seconds": 12.0,
         "start_seconds": (i - 1) * 12.0, "claim_ids": [f"c{i}"], "dates": ["2001"],
         "events": ["A documented event"]}
        for i in range(1, 5)
    ]


class AutonomousDirectionTests(unittest.TestCase):
    def project(self, root: Path) -> Path:
        project = create_project(root, "Director Case").root
        write_json(project / "manifests" / "media_sources.json", {"assets": []})
        return project

    def test_director_plans_complete_story_arc_and_effect_restraint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            plan = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            roles = {item["story_role"] for item in plan["scenes"]}
            self.assertEqual(roles, {"hook", "development", "climax", "outro"})
            self.assertTrue(any(not shot["apply_effect"] for scene in plan["scenes"] for shot in scene["shots"]))
            self.assertTrue((root / "manifests" / "director_report.json").exists())

    def test_critic_scores_every_required_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            (root / "manifests" / "subtitles.srt").write_text("subtitle", encoding="utf-8")
            report = CriticEngine().analyze(root, render_number=1, duration_seconds=48.0)
            self.assertEqual(set(report["scores"]), set(CRITIC_CATEGORIES))
            self.assertTrue(all(0 <= score <= 100 for score in report["scores"].values()))

    def test_low_score_requests_rerender_within_limit(self) -> None:
        policy = QualityPolicy(threshold=85, max_renders=2)
        decision = improvement_decision({"overall_score": 70}, policy, 1)
        self.assertTrue(decision["rerender_required"])

    def test_render_count_and_cost_budgets_stop_loop(self) -> None:
        self.assertFalse(improvement_decision({"overall_score": 70}, QualityPolicy(threshold=85, max_renders=1), 1)["rerender_required"])
        costly = QualityPolicy(threshold=85, max_renders=3, max_rerender_cost_usd=0.1, estimated_rerender_cost_usd=0.2)
        self.assertFalse(improvement_decision({"overall_score": 70}, costly, 1)["rerender_required"])

    def test_feedback_requires_explicit_approval_before_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            record_feedback(root, ["Reduce repetitive transitions."])
            first = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            self.assertEqual(first["director"]["approved_lessons_applied"], [])
            feedback = read_json(root / "manifests" / "critic_feedback.json")
            feedback["entries"][0]["approval_status"] = "approved"
            write_json(root / "manifests" / "critic_feedback.json", feedback)
            second = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            self.assertEqual(second["director"]["approved_lessons_applied"], ["feedback-1"])

    def test_quality_state_recovers_after_interrupted_rerender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            report = {"overall_score": 60, "weak_categories": ["pacing"]}
            decision = improvement_decision(report, QualityPolicy(threshold=80, max_renders=2), 1)
            save_improvement_state(root, decision, report)
            recovered = read_json(root / "manifests" / "quality_cycle.json")
            self.assertEqual(recovered["status"], "rerender_pending")
            self.assertEqual(len(recovered["attempts"]), 1)

    def test_state_and_feedback_writes_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            report = {"overall_score": 60, "weak_categories": ["pacing"]}
            decision = improvement_decision(report, QualityPolicy(threshold=80, max_renders=2), 1)
            save_improvement_state(root, decision, report)
            save_improvement_state(root, decision, report)
            record_feedback(root, ["Improve pacing."])
            record_feedback(root, ["Improve pacing."])
            self.assertEqual(len(read_json(root / "manifests" / "quality_cycle.json")["attempts"]), 1)
            self.assertEqual(len(read_json(root / "manifests" / "critic_feedback.json")["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
