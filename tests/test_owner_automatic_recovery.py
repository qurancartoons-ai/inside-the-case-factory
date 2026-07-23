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

    def test_reference_video_chunks_are_assembled_in_order(self) -> None:
        upload_id = "a" * 32
        for index, content in enumerate((b"first", b"second")):
            environ = {
                "CONTENT_LENGTH": str(len(content)), "wsgi.input": BytesIO(content),
                "HTTP_X_UPLOAD_ID": upload_id, "HTTP_X_FILE_NAME": "documentary.mp4",
                "HTTP_X_CHUNK_INDEX": str(index), "HTTP_X_CHUNK_COUNT": "2",
            }
            response = self.app.upload_reference_chunk(environ)
            self.assertEqual(response[0], "200 OK")

        staged = self.app.staged_reference_upload(upload_id)
        self.assertIsNotNone(staged)
        path, filename = staged or (Path(), "")
        self.assertEqual(filename, "documentary.mp4")
        self.assertEqual(path.read_bytes(), b"firstsecond")

    def test_wizard_consumes_completed_chunked_reference_upload(self) -> None:
        upload_id = "b" * 32
        content = b"local video bytes"
        chunk_environ = {
            "CONTENT_LENGTH": str(len(content)), "wsgi.input": BytesIO(content),
            "HTTP_X_UPLOAD_ID": upload_id, "HTTP_X_FILE_NAME": "documentary.mp4",
            "HTTP_X_CHUNK_INDEX": "0", "HTTP_X_CHUNK_COUNT": "1",
        }
        self.assertEqual(self.app.upload_reference_chunk(chunk_environ)[0], "200 OK")
        fields = {
            "prompt": "Chunked recycle", "duration": "12", "language": "Nederlands",
            "mode": "automatic", "provider_profile": "balanced", "budget": "0.25",
            "workflow_type": "recycle_documentary", "reference_upload_token": upload_id,
        }
        body = urlencode(fields).encode("utf-8")
        environ = {
            "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body),
        }

        with patch("inside_case_factory.web.dashboard.create_reference_documentary") as create_reference, patch(
            "inside_case_factory.web.dashboard.prepare_recycle_documentary"
        ), patch("inside_case_factory.web.dashboard.Thread"):
            response = self.app.create_project_wizard(environ)

        self.assertEqual(response[0], "303 See Other")
        local_path = create_reference.call_args.kwargs["local_path"]
        self.assertEqual(create_reference.call_args.kwargs["original_filename"], "documentary.mp4")
        self.assertFalse(local_path.exists())
        self.assertFalse((self.root / ".upload-staging" / upload_id).exists())

    def test_rejected_dashboard_script_never_remains_as_visible_script(self) -> None:
        project = create_project(self.root / "projects", "Rejected script").root
        write_json(project / "manifests/workflow.json", {"research_approved": True, "language": "Nederlands"})
        write_json(project / "manifests/research_plan.json", {})
        write_json(project / "manifests/dossier.json", {})
        write_json(project / "manifests/narrative_outline.json", {})
        write_json(project / "manifests/story_architecture.json", {})
        write_json(project / "manifests/claims.json", {"claims": []})
        self.app.project_root = lambda slug: project  # type: ignore[method-assign]
        body = urlencode({"target_duration_minutes": "12"}).encode("utf-8")
        environ = {
            "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "wsgi.input": BytesIO(body),
        }

        def write_invalid(*args, **kwargs):
            invalid = {"narration": "veel te kort", "target_duration_minutes": 12, "sections": []}
            write_json(project / "manifests/script.json", invalid)
            return invalid

        with patch("inside_case_factory.web.dashboard.generate_script", side_effect=write_invalid), patch(
            "inside_case_factory.web.dashboard._generate_validated_script_candidates", side_effect=RuntimeError("quality rejected")
        ):
            response = self.app.generate_script(project.name, environ)

        self.assertEqual(response[0], "409 Conflict")
        self.assertFalse((project / "manifests/script.json").exists())


if __name__ == "__main__":
    unittest.main()
