from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import production_progress
from inside_case_factory.utils.files import write_json
from inside_case_factory.web.dashboard import DashboardApp


class DashboardTruthfulProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = create_project(Path(self.tmp.name), "Truthful Dashboard Project")
        self.manifests = self.project.root / "manifests"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_impossible_stage_combination_is_repaired(self) -> None:
        write_json(
            self.manifests / "orchestration.json",
            {
                "version": 1,
                "status": "running",
                "current_stage": "render_video",
                "completed_stages": ["render_video"],
                "waiting_for": "",
                "run_count": 1,
            },
        )

        payload = production_progress(self.project.root)

        self.assertEqual(payload.get("status_repair_message"), "Status wordt hersteld")
        stages = payload.get("stages", [])
        active = [item for item in stages if item.get("status") in {"Bezig", "Wacht op jou", "Geblokkeerd", "Mislukt"}]
        self.assertEqual(len(active), 1)

        seen_gap = False
        for stage in stages:
            if stage.get("status") != "Klaar":
                seen_gap = True
            if seen_gap:
                self.assertNotEqual(stage.get("status"), "Klaar")

    def test_percentage_uses_completed_canonical_stages(self) -> None:
        payload = production_progress(self.project.root)
        self.assertEqual(payload.get("percentage"), 10)

        write_json(
            self.manifests / "orchestration.json",
            {
                "version": 1,
                "status": "running",
                "current_stage": "research",
                "completed_stages": ["research_plan"],
                "waiting_for": "",
                "run_count": 1,
            },
        )
        payload = production_progress(self.project.root)
        self.assertEqual(payload.get("percentage"), 20)

    def test_activity_feed_uses_real_progress_events(self) -> None:
        write_json(
            self.manifests / "progress_events.json",
            {
                "version": 1,
                "events": [
                    {
                        "id": 1,
                        "at": "2026-07-22T10:00:00+00:00",
                        "event": "started",
                        "stage": "research",
                        "message": "Zoekt actuele bronnen",
                    },
                    {
                        "id": 2,
                        "at": "2026-07-22T10:01:00+00:00",
                        "event": "source_found",
                        "stage": "research",
                        "message": "Bron 18 gevonden",
                        "total": 18,
                    },
                    {
                        "id": 3,
                        "at": "2026-07-22T10:02:00+00:00",
                        "event": "completed",
                        "stage": "approve_research",
                        "message": "Research approved",
                    },
                ],
            },
        )

        payload = production_progress(self.project.root)
        activity_texts = [str(item.get("text")) for item in payload.get("activity", [])]
        self.assertIn("Onderzoek gestart", activity_texts)
        self.assertIn("18 bronnen gevonden", activity_texts)
        self.assertIn("Feitencontrole voltooid", activity_texts)

    def test_completed_project_exposes_primary_actions(self) -> None:
        exports = self.project.root / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        (exports / "final_video.mp4").write_bytes(b"video")

        payload = production_progress(self.project.root)
        labels = [str(item.get("label")) for item in payload.get("actions", [])]
        self.assertIn("Video bekijken", labels)
        self.assertIn("Video bewerken", labels)
        self.assertIn("Nieuwe versie renderen", labels)
        self.assertIn("Recycle-documentaire maken", labels)

    def test_page_contains_build_marker(self) -> None:
        app = DashboardApp(Path.cwd())
        html = app.page("Titel", "<section></section>")
        self.assertIn("Dashboard build:", html)


if __name__ == "__main__":
    unittest.main()
