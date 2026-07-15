from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.project import create_project
from inside_case_factory.core.visual_direction import (
    MOTIONS,
    build_cinematic_plan,
    default_visual_style_profile,
    validate_cinematic_plan,
    write_cinematic_plan,
)
from inside_case_factory.pipeline.generator import _mix_sound_design
from inside_case_factory.providers.visual_assets import resolve_scene_assets, rights_are_approved
from inside_case_factory.utils.files import read_json, write_json


def _scene(scene_id: str = "s01", duration: float = 18.0) -> dict[str, object]:
    return {
        "id": scene_id,
        "heading": "The verified timeline",
        "narration": "Approved factual narration.",
        "duration_seconds": duration,
        "start_seconds": 0.0,
        "claim_ids": ["claim-1"],
        "dates": ["2001-01-01"],
        "locations": ["Example City"],
        "events": ["A report documented the event"],
    }


class VisualDirectionTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        return create_project(root, "Visual Case").root

    def test_visual_plan_contains_director_fields_and_bounded_shots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            plan = build_cinematic_plan(project, [_scene()])
            direction = plan["scenes"][0]
            shot = direction["shots"][0]
            self.assertIn("dominant_shot_type", direction)
            self.assertIn("emotional_intensity", direction)
            self.assertIn("transition_to_next", direction)
            self.assertIn("focus_point", shot)
            self.assertIn("framing", shot)
            self.assertIn("text_overlay", shot)
            self.assertIn("document_highlight", shot)
            self.assertLessEqual(max(item["duration_seconds"] for item in direction["shots"]), 9.0)

    def test_motion_and_transition_patterns_vary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            scenes = [{**_scene(f"s{index:02}"), "start_seconds": index * 12} for index in range(1, 6)]
            plan = build_cinematic_plan(project, scenes)
            motions = [shot["motion"] for scene in plan["scenes"] for shot in scene["shots"]]
            transitions = [scene["transition_to_next"] for scene in plan["scenes"]]
            self.assertGreater(len(set(motions)), 3)
            self.assertGreater(len(set(transitions)), 3)

    def test_unknown_rights_are_never_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            image = project / "assets" / "images" / "unknown.jpg"
            image.write_bytes(b"image")
            assets = [{
                "id": "unknown", "type": "image", "path": "assets/images/unknown.jpg", "source_url": "https://archive.test/x",
                "mapped_scenes": ["s01"], "review_status": "approved", "copyright_status": "unknown", "license": "unknown",
            }]
            selected = resolve_scene_assets(project, _scene(), assets)
            self.assertNotIn("unknown", {item["id"] for item in selected})
            self.assertTrue(all(item["rights_status"] == "owned" for item in selected))

    def test_approved_archive_preserves_rights_source_and_claim_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            image = project / "assets" / "images" / "archive.jpg"
            image.write_bytes(b"image")
            assets = [{
                "id": "archive", "type": "image", "path": "assets/images/archive.jpg", "source_url": "https://archive.test/x",
                "mapped_scenes": ["s01"], "review_status": "approved", "rights_status": "public_domain",
                "license": "Public Domain", "claim_ids": ["claim-1"],
            }]
            selected = resolve_scene_assets(project, _scene(), assets)
            archive = next(item for item in selected if item["id"] == "archive")
            self.assertEqual(archive["source_url"], "https://archive.test/x")
            self.assertEqual(archive["license"], "Public Domain")
            self.assertEqual(archive["claim_ids"], ["claim-1"])

    def test_rights_gate_requires_review_and_known_license(self) -> None:
        self.assertFalse(rights_are_approved({"review_status": "pending_review", "license": "Public Domain"}))
        self.assertFalse(rights_are_approved({"review_status": "approved", "license": "unknown"}))
        self.assertTrue(rights_are_approved({"review_status": "approved", "license": "CC-BY 4.0"}))

    def test_safe_generated_fallback_is_owned_and_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            selected = resolve_scene_assets(project, _scene(), [])
            self.assertTrue(selected)
            self.assertTrue(all(item["generated"] for item in selected))
            self.assertTrue(all(item["rights_status"] == "owned" for item in selected))
            self.assertTrue(all(not item["source_url"] for item in selected))

    def test_validator_detects_static_slideshow_and_bad_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            plan = build_cinematic_plan(project, [_scene(duration=6)])
            plan["scenes"][0]["shots"][0]["duration_seconds"] = 15
            plan["scenes"][0]["shots"][0]["motion"] = "static"
            report = validate_cinematic_plan(plan, width=1000, height=1000)
            self.assertFalse(report["valid"])
            self.assertTrue(any("static" in error for error in report["errors"]))
            self.assertTrue(any("aspect ratio" in error for error in report["errors"]))

    def test_validator_detects_repeated_assets_motion_and_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            plan = build_cinematic_plan(project, [_scene("s01"), _scene("s02"), _scene("s03")])
            shots = [shot for scene in plan["scenes"] for shot in scene["shots"]]
            for shot in shots:
                shot["asset"]["id"] = "same"
                shot["motion"] = MOTIONS[0]
            for scene in plan["scenes"]:
                scene["transition_to_next"] = "cross_dissolve"
            errors = validate_cinematic_plan(plan)["errors"]
            self.assertTrue(any("same asset" in error for error in errors))
            self.assertTrue(any("movement" in error for error in errors))
            self.assertTrue(any("transition" in error for error in errors))

    def test_validator_enforces_readable_text_and_voice_dominance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            plan = build_cinematic_plan(project, [_scene(duration=6)])
            plan["scenes"][0]["shots"][0]["text_overlay"] = "x" * 81
            plan["sound_design"]["ducking"] = False
            plan["sound_design"]["effects_peak_db"] = -3
            errors = validate_cinematic_plan(plan)["errors"]
            self.assertTrue(any("unreadably" in error for error in errors))
            self.assertTrue(any("ducking" in error for error in errors))
            self.assertTrue(any("overpower" in error for error in errors))

    def test_sound_cues_are_scene_bound_optional_and_safely_leveled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            plan = build_cinematic_plan(project, [_scene()])
            cues = plan["sound_design"]["cues"]
            self.assertTrue(cues)
            self.assertTrue(all(cue["scene_id"] == "s01" for cue in cues))
            self.assertTrue(all(cue["optional"] for cue in cues))
            self.assertTrue(all(cue["gain_db"] <= -18 for cue in cues))

    def test_audio_mixer_uses_ducked_safe_levels_and_limiter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            voice = project / "assets" / "audio" / "voice.wav"
            voice.parent.mkdir(parents=True, exist_ok=True)
            voice.write_bytes(b"voice")
            sound = project / "assets" / "sound" / "paper.wav"
            sound.parent.mkdir(parents=True, exist_ok=True)
            sound.write_bytes(b"sound")
            design = {"cues": [{"kind": "paper", "start_seconds": 2, "gain_db": -6, "scene_id": "s01"}]}
            with patch("inside_case_factory.pipeline.generator._run") as run:
                used = _mix_sound_design(voice, project, design, project / "assets" / "audio" / "master.wav")
            command = " ".join(run.call_args.args[0])
            self.assertEqual(len(used), 1)
            self.assertIn("volume=-18.0dB", command)
            self.assertIn("adelay=2000|2000", command)
            self.assertIn("sidechaincompress", command)
            self.assertIn("alimiter=limit=0.89", command)

    def test_style_profile_is_persistent_and_plan_generation_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            custom = default_visual_style_profile()
            custom["saturation"] = 0.75
            write_json(project / "manifests" / "visual_style_profile.json", custom)
            first = write_cinematic_plan(project, [_scene()], width=1920, height=1080)
            second = write_cinematic_plan(project, [_scene()], width=1920, height=1080)
            self.assertEqual(first, second)
            self.assertEqual(read_json(project / "manifests" / "visual_style_profile.json")["saturation"], 0.75)
            self.assertTrue(read_json(project / "manifests" / "visual_quality_report.json")["valid"])


if __name__ == "__main__":
    unittest.main()
