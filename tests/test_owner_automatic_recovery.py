from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import production_progress
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


def owner_settings(root: Path) -> Settings:
    return Settings(
        root=root,
        app={}, paths={"projects_dir": "projects"}, video={},
        pipeline={
            "require_paid_api_confirmation": False,
            "owner_automatic_approval": True,
            "default_project_budget_usd": 0.25,
        },
        review_gates={}, script={}, providers={},
    )


class OwnerAutomaticRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = owner_settings(self.root)
        self.settings_patch = patch("inside_case_factory.web.dashboard.load_settings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.app = DashboardApp(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stalled_project(self) -> Path:
        project = create_project(self.root / "projects", "Legacy stalled research").root
        write_json(project / "manifests/provider_config.json", {
            "profile": "offline", "budget_usd": 0.0, "external_calls_enabled": False,
        })
        write_json(project / "manifests/orchestration.json", {
            "status": "approval_required", "current_stage": "research",
            "last_error": "Betaalde zoekactie vereist opnieuw toestemming",
            "completed_stages": ["research"],
        })
        write_json(project / "manifests/production_request.json", {"autonomy_mode": "review"})
        write_json(project / "manifests/production_plan.json", {"autonomy_mode": "review"})
        write_json(project / "manifests/paid_research_approval.json", {"approval_required": True})
        write_json(project / "manifests/paid_api_confirmation.json", {"confirmed": True, "approved_limit_usd": 0.0})
        write_json(project / "manifests/cost_estimate.json", {"project_budget_usd": 0.0, "stages": []})
        return project

    def test_stalled_zero_budget_project_is_migrated_and_has_no_paid_gate(self) -> None:
        project = self.stalled_project()

        self.assertTrue(self.app.migrate_stalled_owner_project(project))

        provider = read_json(project / "manifests/provider_config.json")
        state = read_json(project / "manifests/orchestration.json")
        approval = read_json(project / "manifests/paid_research_approval.json")
        self.assertEqual(provider["budget_usd"], 0.25)
        self.assertEqual(provider["profile"], "balanced")
        self.assertTrue(provider["external_calls_enabled"])
        self.assertEqual(state["status"], "queued")
        self.assertNotIn("research", state["completed_stages"])
        self.assertEqual(approval["resolution"], "superseded_by_owner_budget")
        self.assertFalse(production_progress(project)["paid_gate"]["required"])

    def test_completed_zero_budget_project_is_not_migrated(self) -> None:
        project = self.stalled_project()
        write_json(project / "manifests/orchestration.json", {"status": "completed", "current_stage": "completed"})

        self.assertFalse(self.app.migrate_stalled_owner_project(project))
        self.assertEqual(read_json(project / "manifests/provider_config.json")["budget_usd"], 0.0)

    def test_restart_migrates_and_queues_stalled_project_once(self) -> None:
        project = self.stalled_project()
        self.app.projects = lambda: [project]  # type: ignore[method-assign]

        with patch("inside_case_factory.web.dashboard.Thread") as thread:
            self.app.resume_recoverable_projects()

        thread.assert_called_once()
        self.assertEqual(read_json(project / "manifests/orchestration.json")["status"], "queued")

    def test_recycle_choice_is_preserved_by_wizard_submission(self) -> None:
        fields = {
            "prompt": "Recycle this documentary", "duration": "12", "language": "Nederlands",
            "mode": "automatic", "provider_profile": "balanced", "budget": "0.25",
            "workflow_type": "recycle_documentary", "reference_documentary_url": "https://youtu.be/example",
        }
        body = urlencode(fields).encode("utf-8")
        environ = {
            "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body),
        }

        with patch("inside_case_factory.web.dashboard.create_reference_documentary"), patch(
            "inside_case_factory.web.dashboard.prepare_recycle_documentary"
        ), patch("inside_case_factory.web.dashboard.Thread"):
            response = self.app.create_project_wizard(environ)

        self.assertEqual(response[0], "303 See Other")
        projects = list((self.root / "projects").iterdir())
        request = read_json(projects[0] / "manifests/production_request.json")
        self.assertEqual(request["workflow_type"], "recycle_documentary")


if __name__ == "__main__":
    unittest.main()
