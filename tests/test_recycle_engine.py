from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.recycle import create_reference_documentary, prepare_recycle_documentary
from inside_case_factory.utils.files import read_json
from inside_case_factory.web.dashboard import DashboardApp


SIDE_CAR = {
    "metadata": {
        "title": "Apollo 11 Documentary",
        "channel": "Archive",
        "duration_seconds": 180.0,
    },
    "chapters": [
        {"title": "Launch", "start": 0, "end": 60},
        {"title": "Lunar landing", "start": 60, "end": 120},
        {"title": "Return", "start": 120, "end": 180},
    ],
    "transcript": [
        {"start": 0, "end": 12, "text": "In 1969 NASA launched Apollo 11 from Cape Kennedy."},
        {"start": 64, "end": 78, "text": "Apollo 11 landed on the Moon on July 20 1969."},
        {"start": 130, "end": 145, "text": "The crew returned safely to Earth after the mission ended."},
    ],
}


class RecycleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = create_project(Path(self.temporary.name), "Apollo Reference")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_reference_documentary_creates_manifest_and_blueprint(self) -> None:
        clip = Path(self.temporary.name) / "apollo.mp4"
        clip.write_bytes(b"fake-reference-video")
        sidecar = clip.with_suffix(".mp4.json")
        sidecar.write_text(__import__("json").dumps(SIDE_CAR), encoding="utf-8")

        reference = create_reference_documentary(self.project.root, local_path=clip, original_filename=clip.name)
        blueprint = prepare_recycle_documentary(self.project.root)

        self.assertEqual(reference["input_kind"], "local_mp4")
        self.assertEqual(blueprint["title"], "Apollo 11 Documentary")
        self.assertGreaterEqual(len(blueprint["scenes"]), 3)
        self.assertGreaterEqual(len(blueprint["claims"]), 2)
        self.assertTrue((self.project.root / "manifests" / "recycle_engine_report.md").exists())

    def test_recycle_preparation_seeds_production_request(self) -> None:
        clip = Path(self.temporary.name) / "apollo.mp4"
        clip.write_bytes(b"fake-reference-video")
        clip.with_suffix(".mp4.json").write_text(__import__("json").dumps(SIDE_CAR), encoding="utf-8")

        create_reference_documentary(self.project.root, local_path=clip, original_filename=clip.name)
        prepare_recycle_documentary(self.project.root)

        request = read_json(self.project.root / "manifests" / "production_request.json")
        workflow = read_json(self.project.root / "manifests" / "workflow.json")
        verification = read_json(self.project.root / "manifests" / "recycle_verification_queue.json")

        self.assertEqual(request["workflow_type"], "recycle_documentary")
        self.assertIn("recycle_blueprint", request)
        self.assertTrue(workflow["recycle_analysis_ready"])
        self.assertGreaterEqual(len(verification["claims"]), 2)

    def test_dashboard_wizard_exposes_recycle_inputs(self) -> None:
        page = DashboardApp(Path.cwd()).new_project_wizard()
        self.assertIn("Recycle Documentary", page)
        self.assertIn("reference_documentary_url", page)
        self.assertIn("reference_documentary_file", page)


if __name__ == "__main__":
    unittest.main()