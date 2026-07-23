from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.project import create_project
from inside_case_factory.core.production import _approve_eligible_media, _approve_validated_research, run_production
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
        "prompt": "A factual case", "target_duration_minutes": 5, "language": "English", "run_quality_mode": "sample_or_demo"
    })
    write_json(project.root / "manifests" / "production_plan.json", {
        "topic": "Resumable Case", "autonomy_mode": "automatic", "stages": []
    })
    return project.root


class OrchestrationTests(unittest.TestCase):
    def test_owner_mode_accepts_only_validated_research_and_rights_eligible_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _production_project(Path(tmp))
            write_json(project_root / "manifests" / "sources.json", {"sources": [
                {"id": "valid", "extraction_status": "success", "relevance_status": "relevant"},
                {"id": "failed", "extraction_status": "failed", "relevance_status": "relevant"},
            ]})
            write_json(project_root / "manifests" / "claims.json", {"claims": [
                {"id": "supported", "text": "Supported fact", "source_ids": ["valid"], "evidence": [{"excerpt": "Supported fact"}]},
                {"id": "unsupported", "text": "Unsupported fact", "source_ids": ["failed"], "evidence": []},
            ]})
            write_json(project_root / "manifests" / "media_sources.json", {"assets": [
                {"id": "safe", "review_eligible": True, "rights_status": "public_domain", "suggested_scenes": ["s01"]},
                {"id": "unknown", "review_eligible": True, "rights_status": "unknown"},
            ]})

            self.assertTrue(_approve_validated_research(project_root))
            _approve_eligible_media(project_root)

            sources = read_json(project_root / "manifests" / "sources.json")["sources"]
            claims = read_json(project_root / "manifests" / "claims.json")["claims"]
            media = read_json(project_root / "manifests" / "media_sources.json")["assets"]
            self.assertEqual([item["review_status"] for item in sources], ["approved", "rejected"])
            self.assertEqual([item["review_status"] for item in claims], ["approved", "rejected"])
            self.assertEqual([item["review_status"] for item in media], ["approved", "rejected"])
            self.assertEqual(media[0]["mapped_scenes"], ["s01"])

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
            plan = read_json(project_root / "manifests" / "production_plan.json")
            plan["autonomy_mode"] = "review"
            write_json(project_root / "manifests" / "production_plan.json", plan)

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
                "version": 1,
                "assets": [{
                    "id": "m1",
                    "review_status": "approved",
                    "review_eligible": True,
                    "rights_status": "approved",
                    "relevance_score": 0.9,
                    "source_url": "https://example.com/m1",
                    "project_slug": project_root.name,
                }],
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

            with patch("inside_case_factory.core.production.DirectorEngine.plan", return_value={"scenes": [], "director": {"status": "ready"}}), patch(
                "inside_case_factory.core.production.generate_video_project", side_effect=RuntimeError("crash")
            ):
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    run_production(_settings(root), project_root)
            interrupted = read_json(project_root / "manifests" / "orchestration.json")
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["current_stage"], "render_video")

            with patch("inside_case_factory.core.production.generate_video_project", side_effect=successful_render) as render, patch(
                "inside_case_factory.core.production.generate_scenes"
            ) as scenes, patch("inside_case_factory.core.production.discover_archival_media") as discovery, patch(
                "inside_case_factory.core.production.DirectorEngine.plan", return_value={"scenes": [], "director": {"status": "ready"}}
            ):
                run_production(_settings(root), project_root)
                run_production(_settings(root), project_root)

            completed = read_json(project_root / "manifests" / "orchestration.json")
            self.assertEqual(render.call_count, 1)
            scenes.assert_not_called()
            discovery.assert_not_called()
            self.assertEqual(completed["status"], "demo_completed")
            self.assertIn("render_video", completed["completed_stages"])

    def test_evidence_grade_blocks_when_research_foundation_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            request = read_json(project_root / "manifests" / "production_request.json")
            request["run_quality_mode"] = "evidence_grade"
            write_json(project_root / "manifests" / "production_request.json", request)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow["research_approved"] = True
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research.json", {"status": "not_started"})
            write_json(project_root / "manifests" / "sources.json", {"sources": []})
            write_json(project_root / "manifests" / "claims.json", {"claims": []})

            run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            quality = read_json(project_root / "manifests" / "quality_cycle.json")
            self.assertEqual(state["status"], "blocked_missing_research")
            self.assertEqual(quality["latest_foundation_gate"]["stage"], "research")
            self.assertFalse(quality["latest_foundation_gate"]["passed"])

    def test_evidence_grade_blocks_when_no_approved_source_linked_claims_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            request = read_json(project_root / "manifests" / "production_request.json")
            request["run_quality_mode"] = "evidence_grade"
            write_json(project_root / "manifests" / "production_request.json", request)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow["research_approved"] = True
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research.json", {"status": "completed"})
            write_json(project_root / "manifests" / "sources.json", {
                "sources": [{"id": "s1", "review_status": "approved", "relevance_status": "relevant", "url": "https://example.com/source"}]
            })
            write_json(project_root / "manifests" / "claims.json", {
                "claims": [{"id": "c1", "review_status": "approved", "source_ids": ["missing-source"]}]
            })

            run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            quality = read_json(project_root / "manifests" / "quality_cycle.json")
            self.assertEqual(state["status"], "blocked_missing_claims")
            self.assertEqual(quality["latest_foundation_gate"]["stage"], "claims")
            self.assertEqual(quality["latest_foundation_gate"]["blocking_code"], "missing_approved_claims")

    def test_evidence_grade_blocks_when_no_eligible_media_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            request = read_json(project_root / "manifests" / "production_request.json")
            request["run_quality_mode"] = "evidence_grade"
            write_json(project_root / "manifests" / "production_request.json", request)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow.update({"research_approved": True, "script_approved": True, "scenes_generated": True})
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research.json", {"status": "completed"})
            write_json(project_root / "manifests" / "sources.json", {
                "sources": [{"id": "s1", "review_status": "approved", "relevance_status": "relevant", "url": "https://example.com/source"}]
            })
            write_json(project_root / "manifests" / "claims.json", {
                "claims": [{"id": "c1", "review_status": "approved", "source_ids": ["s1"]}]
            })
            write_json(project_root / "manifests" / "research_plan.json", {"version": 1})
            write_json(project_root / "manifests" / "script.json", {"version": 1, "status": "approved", "narration": "Approved"})
            write_json(project_root / "manifests" / "scenes.json", {"version": 1, "scenes": [{"id": "s01", "narration": "Approved"}]})
            write_json(project_root / "manifests" / "media_sources.json", {"version": 1, "assets": []})

            with patch("inside_case_factory.core.production.discover_project_scene_media"):
                run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            quality = read_json(project_root / "manifests" / "quality_cycle.json")
            self.assertEqual(state["status"], "blocked_missing_media")
            self.assertEqual(quality["latest_foundation_gate"]["stage"], "media")

    def test_sample_mode_allows_demo_completion_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow.update({"research_approved": True, "script_approved": True, "scenes_generated": True})
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research_plan.json", {"version": 1})
            write_json(project_root / "manifests" / "research.json", {"status": "completed"})
            write_json(project_root / "manifests" / "sources.json", {"sources": [{"id": "s1", "review_status": "approved", "relevance_status": "relevant", "url": "https://example.com/source"}]})
            write_json(project_root / "manifests" / "claims.json", {"claims": [{"id": "c1", "review_status": "approved", "source_ids": ["s1"]}]})
            write_json(project_root / "manifests" / "script.json", {"version": 1, "title": "Demo", "status": "approved", "narration": "Approved narration."})
            write_json(project_root / "manifests" / "scenes.json", {"version": 1, "scenes": [{"id": "s01", "narration": "Approved narration."}]})
            write_json(project_root / "manifests" / "media_sources.json", {"version": 1, "assets": []})

            def successful_render(*args: object, **kwargs: object) -> None:
                output = project_root / "exports" / "final_video.mp4"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"video")

            with patch("inside_case_factory.core.production.discover_project_scene_media"), patch(
                "inside_case_factory.core.production.generate_video_project", side_effect=successful_render
            ):
                run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            workflow = read_json(project_root / "manifests" / "workflow.json")
            self.assertEqual(state["status"], "demo_completed")
            self.assertEqual(workflow["run_outcome_status"], "demo_completed")
            self.assertNotEqual(workflow["run_outcome_status"], "evidence_grade_completed")

    def test_evidence_grade_valid_foundation_advances_to_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = _production_project(root)
            request = read_json(project_root / "manifests" / "production_request.json")
            request["run_quality_mode"] = "evidence_grade"
            write_json(project_root / "manifests" / "production_request.json", request)
            workflow = read_json(project_root / "manifests" / "workflow.json")
            workflow.update({"research_approved": True, "script_approved": True, "scenes_generated": True})
            write_json(project_root / "manifests" / "workflow.json", workflow)
            write_json(project_root / "manifests" / "research_plan.json", {"version": 1})
            write_json(project_root / "manifests" / "research.json", {"status": "completed"})
            write_json(project_root / "manifests" / "sources.json", {"sources": [{"id": "s1", "review_status": "approved", "relevance_status": "relevant", "url": "https://example.com/source"}]})
            write_json(project_root / "manifests" / "claims.json", {"claims": [{"id": "c1", "review_status": "approved", "source_ids": ["s1"]}]})
            write_json(project_root / "manifests" / "script.json", {"version": 1, "title": "Evidence", "status": "approved", "narration": "Approved narration."})
            write_json(project_root / "manifests" / "scenes.json", {"version": 1, "scenes": [{"id": "s01", "narration": "Approved narration."}]})
            write_json(project_root / "manifests" / "media_sources.json", {"version": 1, "assets": [{"id": "m1", "review_status": "approved", "review_eligible": True, "rights_status": "approved"}]})

            def successful_render(*args: object, **kwargs: object) -> None:
                output = project_root / "exports" / "final_video.mp4"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"video")

            with patch("inside_case_factory.core.production.discover_project_scene_media"), patch(
                "inside_case_factory.core.production.generate_video_project", side_effect=successful_render
            ), patch("inside_case_factory.core.production.DirectorEngine.plan", return_value={"scenes": [], "director": {"status": "ready"}}):
                run_production(_settings(root), project_root)

            state = read_json(project_root / "manifests" / "orchestration.json")
            workflow = read_json(project_root / "manifests" / "workflow.json")
            self.assertEqual(state["status"], "evidence_grade_completed")
            self.assertEqual(workflow["run_outcome_status"], "evidence_grade_completed")


if __name__ == "__main__":
    unittest.main()
