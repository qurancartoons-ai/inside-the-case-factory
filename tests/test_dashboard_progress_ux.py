from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.progress import EVENT_TYPES, TaskQueue, write_progress_event
from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import PHASES, production_progress
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


class DashboardProgressUXTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Quiet Studio").root
        self.app = DashboardApp(Path.cwd())
        self.app.project_root = lambda slug: self.root  # type: ignore[method-assign]

    def tearDown(self):
        self.temporary.cleanup()

    def test_modern_main_structure_has_only_five_navigation_choices(self):
        html = self.app.page("Studio", "<p>Rust</p>")
        nav = html.split('<nav class="main-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(nav.count("<a "), 5)
        for label in ("Projecten", "Nieuwe documentaire", "Voortgang", "Review", "Instellingen"):
            self.assertIn(label, nav)
        self.assertIn("Documentaire Studio", html)

    def test_pipeline_has_exact_user_facing_steps_and_one_active_step(self):
        result = production_progress(self.root)
        self.assertEqual(tuple(item["name"] for item in result["phases"]), PHASES)
        self.assertEqual(PHASES, ("Intake", "Onderzoek", "Claims", "Script", "Producer", "Director", "Media", "Voice-over", "Montage", "Review"))
        self.assertLessEqual(sum(item["status"] == "actief" for item in result["phases"]), 1)

    def test_queue_allows_only_one_heavy_task_and_light_parallel_work(self):
        queue = TaskQueue(self.root)
        first = queue.enqueue("research", "Bronnen onderzoeken")
        second = queue.enqueue("render", "Film monteren")
        light = queue.enqueue("metadata", "Titel voorbereiden", heavy=False)
        self.assertEqual(first["status"], "active")
        self.assertEqual(second["status"], "waiting")
        self.assertEqual(light["status"], "active")

    def test_stall_detection_explains_and_exposes_control_actions(self):
        queue = TaskQueue(self.root, stall_after_seconds=30)
        task = queue.enqueue("research", "Bronnen verwerken")
        data = read_json(queue.path)
        data["tasks"][0]["updated_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        write_json(queue.path, data)
        with patch.object(Path, "rglob", return_value=iter(())):
            stalled = queue.snapshot()["blocked"][0]
        self.assertEqual(stalled["status"], "possibly_stalled")
        self.assertIn("Geen nieuwe voortgang", stalled["reason"])
        html = self.app.production_overview_page("quiet-studio")
        for label in ("Mogelijk vastgelopen", "Hervatten", "Opnieuw proberen", "Log bekijken", "Taak stoppen"):
            self.assertIn(label, html)
        queue.action(task["id"], "retry")
        self.assertEqual(read_json(queue.path)["tasks"][0]["retries"], 1)
        queue.action(task["id"], "stop")
        self.assertEqual(read_json(queue.path)["tasks"][0]["status"], "stopped")

    def test_structured_progress_events_and_research_substatus_are_visible(self):
        for event in EVENT_TYPES:
            write_progress_event(self.root, event, "research", f"event {event}")
        write_progress_event(self.root, "source_found", "research", "Bron 4 van 12 wordt verwerkt")
        result = production_progress(self.root)
        self.assertEqual(result["research"]["current_task"], "Bron 4 van 12 wordt verwerkt")
        self.assertTrue({item["event"] for item in result["events"]}.issuperset(EVENT_TYPES))
        html = self.app.production_overview_page("quiet-studio")
        for field in ("sources_found", "sources_processed", "draft_claims", "estimated_remaining", "last_error"):
            self.assertIn(field, html)

    def test_progress_route_is_a_fast_shell_without_manifest_reads_or_providers(self):
        original = Path.read_text; reads = []
        def counted(path, *args, **kwargs): reads.append(path); return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", counted), patch("urllib.request.urlopen") as provider:
            html = self.app.production_overview_page("quiet-studio")
        self.assertEqual(reads, [])
        provider.assert_not_called()
        self.assertLess(len(html.encode()), 30000)
        self.assertIn("Voortgang wordt geladen", html)
        self.assertIn("setInterval(refresh,3000)", html)

    def test_mobile_layout_and_technical_details_are_calmly_hidden(self):
        html = self.app.page("Studio", "<p>Rust</p>")
        self.assertIn("max-width: 620px", html)
        self.assertIn(".main-nav", html)
        project = self.app.project_detail("quiet-studio")
        self.assertIn("<summary>Geavanceerd</summary>", project)
        visible = project.split("<summary>Geavanceerd</summary>", 1)[0]
        self.assertNotIn("manifest", visible.lower())
        self.assertNotIn("orchestration", visible.lower())


if __name__ == "__main__":
    unittest.main()
