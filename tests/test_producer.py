from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.autonomous_direction import CriticEngine, DirectorEngine
from inside_case_factory.core.producer import BLUEPRINT_ROLES, PRODUCER_CATEGORIES, ProducerEngine
from inside_case_factory.core.project import create_project
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


def producer_scenes() -> list[dict[str, object]]:
    return [
        {
            "id": f"s{index:02}", "heading": f"Section {index}",
            "narration": f"Documented detail {index} changes how the timeline is understood.",
            "duration_seconds": 20.0, "start_seconds": (index - 1) * 20.0,
            "claim_ids": [f"c{index}"], "dates": ["2001"], "events": ["A documented event"],
            **({"speaker": "Witness"} if index in {3, 5, 7} else {}),
        }
        for index in range(1, 9)
    ]


class ProducerEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Producer Case").root
        write_json(self.root / "manifests/media_sources.json", {"assets": []})
        self.engine = ProducerEngine()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_blueprint_contains_complete_documentary_structure(self) -> None:
        plan = self.engine.plan(self.root, producer_scenes())
        self.assertEqual(tuple(item["role"] for item in plan["documentary_structure"]), BLUEPRINT_ROLES)
        self.assertTrue(all("purpose" in section and "ratios" in section for section in plan["sections"]))

    def test_pacing_detects_long_explanation_voice_over_and_repetition(self) -> None:
        scenes = producer_scenes()
        scenes[0]["duration_seconds"] = 100
        scenes[0]["narration"] = " ".join(["evidence"] * 300)
        scenes[1]["narration"] = scenes[2]["narration"]
        plan = self.engine.plan(self.root, scenes)
        kinds = {item["kind"] for item in plan["pacing_analysis"]["issues"]}
        self.assertIn("explanation_too_long", kinds)
        self.assertIn("repetition", kinds)
        self.assertIn("too_much_voice_over", kinds)

    def test_interview_planner_controls_voice_subtitles_broll_and_return(self) -> None:
        plan = self.engine.plan(self.root, producer_scenes())
        interview = plan["interview_plan"][0]
        self.assertLessEqual(interview["maximum_duration_seconds"], 45)
        self.assertTrue(interview["subtitles_from_interview_start"])
        self.assertGreater(interview["b_roll_overlay"]["end_fraction"], interview["b_roll_overlay"]["start_fraction"])
        self.assertEqual(interview["return_to_speaker_fraction"], interview["b_roll_overlay"]["end_fraction"])

    def test_emotional_arc_scores_every_scene_and_dimension(self) -> None:
        arc = self.engine.plan(self.root, producer_scenes())["emotional_arc"]
        self.assertEqual(len(arc), 8)
        self.assertTrue(all(all(0 <= item[key] <= 100 for key in ("tension", "curiosity", "emotion", "impact", "clarity")) for item in arc))
        self.assertGreater(max(item["tension"] for item in arc), min(item["tension"] for item in arc))

    def test_retention_curve_declines_and_rewards_late_climax(self) -> None:
        plan = self.engine.plan(self.root, producer_scenes())
        curve = plan["retention_curve"]
        self.assertEqual(len(curve), 8)
        self.assertGreater(curve[0]["estimated_retention"], curve[-1]["estimated_retention"])
        climax_index = next(i for i, section in enumerate(plan["sections"]) if section["role"] == "climax")
        self.assertGreaterEqual(climax_index / 7, .65)

    def test_attention_engine_flags_overload_and_requests_replan(self) -> None:
        scenes = producer_scenes()
        scenes[4]["narration"] = " ".join(["dense"] * 120)
        scenes[4]["duration_seconds"] = 8
        attention = self.engine.plan(self.root, scenes)["attention_analysis"]
        self.assertTrue(attention["director_replan_recommended"])
        self.assertTrue(any("information_overload" in risk["reasons"] for risk in attention["risk_points"]))

    def test_producer_blueprint_drives_director_roles_ratios_and_rhythm(self) -> None:
        blueprint = self.engine.plan(self.root, producer_scenes())
        directed = DirectorEngine().plan(self.root, producer_scenes(), width=1920, height=1080)
        producer_by_id = {item["id"]: item for item in blueprint["sections"]}
        for scene in directed["scenes"]:
            source = producer_by_id[scene["scene_id"]]
            self.assertEqual(scene["story_role"], source["role"])
            self.assertEqual(scene["producer_ratios"], source["ratios"])

    def test_producer_reviews_critic_and_scopes_improvement_loop(self) -> None:
        self.engine.plan(self.root, producer_scenes())
        DirectorEngine().plan(self.root, producer_scenes(), width=1920, height=1080)
        (self.root / "manifests/subtitles.srt").write_text("subtitle", encoding="utf-8")
        critic = CriticEngine().analyze(self.root, render_number=1, duration_seconds=160)
        report = self.engine.review_render(self.root, critic)
        self.assertEqual(set(report["scores"]), set(PRODUCER_CATEGORIES))
        self.assertTrue(all("Revise only" in item for item in report["improvement_plan"]))
        self.assertEqual(report["critic_validation_score"], critic["overall_score"])

    def test_crash_recovery_rebuilds_missing_report_from_blueprint(self) -> None:
        self.engine.plan(self.root, producer_scenes())
        report_path = self.root / "manifests/producer_report.json"
        report_path.unlink(missing_ok=True)
        report = self.engine.review_render(self.root, {"render_number": 1, "overall_score": 85, "scores": {"tension_arc": 85}})
        self.assertTrue(report_path.exists())
        self.assertEqual(report["render_number"], 1)

    def test_planning_and_review_are_idempotent(self) -> None:
        first = self.engine.plan(self.root, producer_scenes())
        second = self.engine.plan(self.root, producer_scenes())
        self.assertEqual(first["sections"], second["sections"])
        self.assertEqual(first["retention_curve"], second["retention_curve"])
        critic = {"render_number": 1, "overall_score": 88, "scores": {"tension_arc": 88}}
        self.engine.review_render(self.root, critic)
        self.engine.review_render(self.root, critic)
        self.assertEqual(read_json(self.root / "manifests/producer_report.json")["render_number"], 1)

    def test_dashboard_shows_producer_graphs_ratios_retention_and_structure(self) -> None:
        self.engine.plan(self.root, producer_scenes())
        DirectorEngine().plan(self.root, producer_scenes(), width=1920, height=1080)
        (self.root / "manifests/subtitles.srt").write_text("subtitle", encoding="utf-8")
        critic = CriticEngine().analyze(self.root, render_number=1, duration_seconds=160)
        self.engine.review_render(self.root, critic)
        page = DashboardApp(Path.cwd()).direction_reports(self.root, "producer-case")
        for label in ("Spanningsgrafiek", "Emotiegrafiek", "Voice-over", "Interview", "B-roll", "Geschatte retentiecurve", "Documentairestructuur"):
            self.assertIn(label, page)


if __name__ == "__main__":
    unittest.main()
