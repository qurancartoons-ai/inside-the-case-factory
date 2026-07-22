from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.recycle import _parse_vtt, create_reference_documentary, prepare_recycle_documentary
from inside_case_factory.core.research import add_claim, add_source, approve_research, review_item
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
        self.assertTrue(blueprint["coverage"]["complete_transcript_coverage"])
        self.assertTrue(blueprint["coverage"]["complete_temporal_coverage"])
        self.assertLessEqual(blueprint["coverage"]["maximum_window_seconds"], 45.0)
        self.assertTrue((self.project.root / "manifests" / "recycle_engine_report.md").exists())

    def test_long_chapters_are_split_without_losing_transcript_segments(self) -> None:
        clip = Path(self.temporary.name) / "long.mp4"
        clip.write_bytes(b"fake-reference-video")
        sidecar = {
            "metadata": {"title": "Volledige documentaire", "duration_seconds": 180.0},
            "chapters": [{"title": "Een lang hoofdstuk", "start": 0, "end": 180}],
            "transcript": [
                {"id": f"s{index}", "start": index * 30, "end": index * 30 + 20, "text": f"In 198{index} werd gebeurtenis {index} officieel vastgelegd door Archief Nederland."}
                for index in range(6)
            ],
        }
        clip.with_suffix(".mp4.json").write_text(__import__("json").dumps(sidecar), encoding="utf-8")

        create_reference_documentary(self.project.root, local_path=clip)
        blueprint = prepare_recycle_documentary(self.project.root)

        self.assertEqual(blueprint["coverage"]["window_count"], 4)
        self.assertEqual(blueprint["coverage"]["assigned_transcript_segments"], 6)
        self.assertEqual(blueprint["coverage"]["transcript_coverage_ratio"], 1.0)
        self.assertTrue(any("werd" in item["statement"] for item in blueprint["claims"]))

    def test_vtt_caption_fallback_normalizes_and_deduplicates_cues(self) -> None:
        transcript = _parse_vtt(
            """WEBVTT

00:00:00.000 --> 00:00:03.000
<c>De documentaire begint.</c>

00:00:03.000 --> 00:00:06.000
De documentaire begint.

00:00:06.000 --> 00:00:09.500
Daarna werd in 1983 het onderzoek geopend.
"""
        )
        self.assertEqual(len(transcript), 2)
        self.assertEqual(transcript[0]["text"], "De documentaire begint.")
        self.assertEqual(transcript[1]["start"], 6.0)

    def test_truncated_transcript_cannot_claim_complete_documentary_coverage(self) -> None:
        clip = Path(self.temporary.name) / "truncated.mp4"
        clip.write_bytes(b"fake-reference-video")
        sidecar = {
            "metadata": {"title": "Afgebroken transcript", "duration_seconds": 180.0},
            "transcript": [{"start": 0, "end": 20, "text": "In 1983 werd het eerste deel van deze documentaire opgenomen."}],
        }
        clip.with_suffix(".mp4.json").write_text(__import__("json").dumps(sidecar), encoding="utf-8")
        create_reference_documentary(self.project.root, local_path=clip)
        with self.assertRaisesRegex(RuntimeError, "could not prove complete documentary coverage"):
            prepare_recycle_documentary(self.project.root)

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
        self.assertFalse(workflow["recycle_verification_ready"])
        self.assertFalse(workflow["recycle_reconstruction_ready"])
        self.assertGreaterEqual(len(verification["claims"]), 2)

    def test_independent_source_approval_updates_recycle_verification(self) -> None:
        clip = Path(self.temporary.name) / "apollo.mp4"
        clip.write_bytes(b"fake-reference-video")
        clip.with_suffix(".mp4.json").write_text(__import__("json").dumps(SIDE_CAR), encoding="utf-8")
        create_reference_documentary(self.project.root, local_path=clip)
        blueprint = prepare_recycle_documentary(self.project.root)
        source = add_source(
            self.project.root,
            title="NASA mission history",
            url="https://example.org/nasa-history",
            publisher="NASA",
        )
        claim = add_claim(
            self.project.root,
            text=blueprint["claims"][0]["statement"],
            source_ids=[source["id"]],
        )
        review_item(self.project.root, "sources.json", "sources", source["id"], "approved")
        review_item(self.project.root, "claims.json", "claims", claim["id"], "approved")

        self.assertTrue(approve_research(self.project.root))
        verification = read_json(self.project.root / "manifests/recycle_verification_queue.json")
        workflow = read_json(self.project.root / "manifests/workflow.json")
        self.assertTrue(verification["ready"])
        self.assertEqual(verification["verified_count"], 1)
        self.assertTrue(workflow["recycle_verification_ready"])

    def test_dashboard_wizard_exposes_recycle_inputs(self) -> None:
        page = DashboardApp(Path.cwd()).new_project_wizard()
        self.assertIn("Recycle Documentary", page)
        self.assertIn("reference_documentary_url", page)
        self.assertIn("reference_documentary_file", page)


if __name__ == "__main__":
    unittest.main()
