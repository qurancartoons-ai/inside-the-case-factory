from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.research import approve_script, script_content_hash
from inside_case_factory.pipeline.generator import _approved_project, _append_rerender_history
from inside_case_factory.utils.files import read_json, write_json


class RerenderCycleTests(unittest.TestCase):
    def _prepare_factual_project(self, tmp: str) -> Path:
        project = create_project(Path(tmp), "Rerender Case").root
        script = {
            "version": 1,
            "title": "Rerender Case",
            "status": "approved",
            "narration": "Approved factual narration.",
            "sections": [{"id": "sec01", "text": "Approved factual narration."}],
        }
        write_json(project / "manifests" / "script.json", script)
        write_json(project / "manifests" / "scenes.json", {"version": 1, "scenes": [{"id": "s01", "narration": "Approved factual narration."}]})
        write_json(project / "manifests" / "media_sources.json", {"version": 1, "assets": [{"id": "m1", "review_status": "approved"}]})
        workflow = read_json(project / "manifests" / "workflow.json")
        workflow.update({"script_approved": True, "scenes_generated": True})
        write_json(project / "manifests" / "workflow.json", workflow)
        return project

    def test_unchanged_script_rerender_reuses_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._prepare_factual_project(tmp)
            script = read_json(project / "manifests" / "script.json")
            fingerprint = {
                "script_hash": script_content_hash(script),
                "approved_at": "2026-01-01T00:00:00+00:00",
                "approval_source": "manual_review",
                "approval_valid": True,
            }
            script["approval_fingerprint"] = fingerprint
            write_json(project / "manifests" / "script.json", script)
            workflow = read_json(project / "manifests" / "workflow.json")
            workflow["script_approved"] = False
            workflow["script_approval_fingerprint"] = fingerprint
            write_json(project / "manifests" / "workflow.json", workflow)

            _approved_project(project)

            updated_workflow = read_json(project / "manifests" / "workflow.json")
            self.assertTrue(updated_workflow["script_approved"])

    def test_changed_script_requires_reapproval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._prepare_factual_project(tmp)
            script = read_json(project / "manifests" / "script.json")
            fingerprint = {
                "script_hash": script_content_hash(script),
                "approved_at": "2026-01-01T00:00:00+00:00",
                "approval_source": "manual_review",
                "approval_valid": True,
            }
            script["approval_fingerprint"] = fingerprint
            script["narration"] = "Changed narration after approval."
            write_json(project / "manifests" / "script.json", script)
            workflow = read_json(project / "manifests" / "workflow.json")
            workflow["script_approval_fingerprint"] = fingerprint
            write_json(project / "manifests" / "workflow.json", workflow)

            with self.assertRaisesRegex(RuntimeError, "explicitly approved"):
                _approved_project(project)

    def test_approve_script_persists_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Approve Fingerprint").root
            write_json(project / "manifests" / "script.json", {
                "version": 1,
                "title": "Approve Fingerprint",
                "status": "draft",
                "narration": "Narration ready for approval.",
                "sections": [{"id": "sec01", "text": "Narration ready for approval."}],
            })

            self.assertTrue(approve_script(project))

            script = read_json(project / "manifests" / "script.json")
            workflow = read_json(project / "manifests" / "workflow.json")
            self.assertIn("approval_fingerprint", script)
            self.assertEqual(script["approval_fingerprint"]["script_hash"], script_content_hash(script))
            self.assertIn("script_approval_fingerprint", workflow)
            self.assertTrue(workflow["script_approval_fingerprint"]["approval_valid"])

    def test_multiple_rerender_history_entries_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "History Case").root
            manifests = project / "manifests"
            _append_rerender_history(
                manifests,
                iteration=2,
                reason="Score below threshold",
                changed_artifacts=["manifests/director_plan.json", "exports/final_video.mp4"],
                quality_delta=None,
            )
            _append_rerender_history(
                manifests,
                iteration=3,
                reason="Tension arc still weak",
                changed_artifacts=["manifests/director_plan.json", "manifests/critic_report.json"],
                quality_delta=4.5,
            )
            cycle = read_json(manifests / "quality_cycle.json")
            history = cycle.get("rerender_history", [])
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["iteration"], 2)
            self.assertEqual(history[1]["quality_delta"], 4.5)

    def test_existing_approved_script_without_fingerprint_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._prepare_factual_project(tmp)
            script = read_json(project / "manifests" / "script.json")
            script.pop("approval_fingerprint", None)
            write_json(project / "manifests" / "script.json", script)
            workflow = read_json(project / "manifests" / "workflow.json")
            workflow.pop("script_approval_fingerprint", None)
            write_json(project / "manifests" / "workflow.json", workflow)

            _approved_project(project)

            updated_script = read_json(project / "manifests" / "script.json")
            updated_workflow = read_json(project / "manifests" / "workflow.json")
            self.assertIn("approval_fingerprint", updated_script)
            self.assertIn("script_approval_fingerprint", updated_workflow)


if __name__ == "__main__":
    unittest.main()
