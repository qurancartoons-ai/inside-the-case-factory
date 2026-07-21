from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.progress import EVENT_TYPES, TaskQueue, write_progress_event
from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import production_progress
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

    def test_main_navigation_is_simplified_to_two_working_choices(self):
        html = self.app.page("Studio", "<p>Rust</p>")
        nav = html.split('<nav class="main-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(nav.count("<a "), 2)
        for label in ("Projecten", "Nieuwe documentaire"):
            self.assertIn(label, nav)
        self.assertNotIn("Voortgang", nav)
        self.assertNotIn("Instellingen", nav)
        self.assertIn("Documentaire Studio", html)

    def test_root_route_redirects_to_projects(self):
        status, headers, _ = self.app.dispatch({"REQUEST_METHOD": "GET", "PATH_INFO": "/"})
        self.assertEqual(status, "303 See Other")
        self.assertIn(("Location", "/projects"), headers)

    def test_progress_shell_contains_required_user_facing_stage_names(self):
        html = self.app.production_overview_page("quiet-studio")
        for label in ("Onderwerp", "Onderzoek", "Feitencontrole", "Script", "Beelden", "Montage", "Eindcontrole", "Voltooid"):
            self.assertIn(label, html)

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
        for label in ("Hervatten", "Opnieuw proberen", "Taak stoppen"):
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
        for field in ("bronnen gevonden", "claims in concept", "Technische details"):
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
        self.assertIn("<summary>Technische details</summary>", project)
        visible = project.split("<summary>Technische details</summary>", 1)[0]
        self.assertNotIn("manifest", visible.lower())
        self.assertNotIn("orchestration", visible.lower())

    def test_projects_page_shows_status_fields_and_resume_actions(self):
        html = self.app.index()
        for label in ("Onderwerp", "Aangemaakt", "Laatst gewijzigd", "Workflowfase", "Voortgang", "Status", "Goedkeuring nodig", "Openen", "Doorgaan"):
            self.assertIn(label, html)

    def test_project_checkpoint_is_saved_immediately_for_new_work(self):
        self.app.persist_project_checkpoint(self.root, current_stage="Onderwerp", latest_user_input="Maak een docu over test")
        state = read_json(self.root / "manifests" / "dashboard_state.json")
        self.assertEqual(state["current_workflow_stage"], "Onderwerp")
        self.assertEqual(state["latest_user_input"], "Maak een docu over test")
        self.assertIn("project_id", state)
        self.assertIn("created_at", state)
        self.assertIn("updated_at", state)

    def test_safe_back_does_not_cancel_running_task(self):
        queue = TaskQueue(self.root)
        queue.enqueue("research", "Bronnen verwerken")
        status, _, body = self.app.navigate_back("quiet-studio")
        self.assertEqual(status, "200 OK")
        html = body.decode("utf-8")
        self.assertIn("This task is still running.", html)
        self.assertIn("Your progress has been saved.", html)
        self.assertIn("Terug naar projecten", html)
        self.assertIn("Hier blijven", html)


if __name__ == "__main__":
    unittest.main()
