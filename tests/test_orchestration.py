from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.project import create_project
from inside_case_factory.core.production import run_production
from inside_case_factory.pipeline.generator import _ensure_voice_segment
from inside_case_factory.utils.files import read_json, write_json


def _settings(root: Path) -> Settings:
    return Settings(
        root=root,
        app={},
        paths={"projects_dir": "projects"},
        video={"width": 320, "height": 180, "fps": 12},
        pipeline={},
        review_gates={},
        script={},
        providers={},
    )


def _production_project(root: Path) -> Path:
    project = create_project(root / "projects", "Resumable Case")
    write_json(project.root / "manifests" / "production_request.json", {
        "prompt": "A factual case", "target_duration_minutes": 5, "language": "English"
    })
    write_json(project.root / "manifests" / "production_plan.json", {
        "topic": "Resumable Case", "autonomy_mode": "automatic", "stages": []
    })
    return project.root


class OrchestrationTests(unittest.TestCase):
    def test_existing_voice_segment_is_not_generated_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "voice.wav"
            wav.write_bytes(b"complete audio")
            voice = Mock()

            _ensure_voice_segment(voice, "Narration", wav, root / "voice.txt")

            voice.synthesize_to_file.assert_not_called()

    def test_completed_stage_is_not_executed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)

            def research(*args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "claims_added": 1}

            with patch("inside_case_factory.core.production.run_research", side_effect=research) as mocked:
                run_production(_settings(root), project_root)
                run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            self.assertEqual(mocked.call_count, 1)
            self.assertIn("research", state["completed_stages"])
            self.assertEqual(state["status"], "waiting_for_approval")
            self.assertEqual(state["waiting_for"], "research_approval")

    def test_crashed_render_resumes_without_repeating_previous_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow.update({"research_approved": True, "script_approved": True, "scenes_generated": True})
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research_plan.json", {"version": 1})
            write_json(project_root / "manifests" / "script.json", {
                "version": 1, "title": "Resumable Case", "status": "approved", "narration": "Approved narration."
            })
            write_json(project_root / "manifests" / "scenes.json", {
                "version": 1, "scenes": [{"id": "s01", "narration": "Approved narration."}]
            })
            write_json(project_root / "manifests" / "media_sources.json", {
                "version": 1, "assets": [{"id": "m1", "review_status": "approved"}]
            })
            write_json(project_root / "manifests" / "orchestration.json", {
                "version": 1,
                "status": "running",
                "current_stage": "media_approval",
                "completed_stages": [
                    "research_plan", "research", "research_approval", "generate_script",
                    "script_approval", "generate_scenes", "discover_media", "media_approval",
                ],
                "run_count": 1,
            })

            def successful_render(*args: object, **kwargs: object) -> None:
                output = project_root / "exports" / "final_video.mp4"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"video")

            with patch("inside_case_factory.core.production.generate_video_project", side_effect=RuntimeError("crash")):
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    run_production(_settings(root), project_root)
            interrupted = read_json(project_root / "manifests" / "orchestration.json")
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["current_stage"], "render_video")

            with patch("inside_case_factory.core.production.generate_video_project", side_effect=successful_render) as render, patch(
                "inside_case_factory.core.production.generate_scenes"
            ) as scenes, patch("inside_case_factory.core.production.discover_archival_media") as discovery:
                run_production(_settings(root), project_root)
                run_production(_settings(root), project_root)

            completed = read_json(project_root / "manifests" / "orchestration.json")
            self.assertEqual(render.call_count, 1)
            scenes.assert_not_called()
            discovery.assert_not_called()
            self.assertEqual(completed["status"], "completed")
            self.assertIn("render_video", completed["completed_stages"])


if __name__ == "__main__":
    unittest.main()
