from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.draft_review import approve_scene, create_review_draft, revise_draft
from inside_case_factory.core.project import create_project
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


class DraftReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Review Case").root
        scenes = [{
            "id": f"s{i:02}", "index": i, "heading": "Intro" if i == 1 else "Outro" if i == 4 else f"Scene {i}",
            "narration": f"Voice-over text for scene {i}.", "duration_seconds": 30.0,
            "claim_ids": [f"c{i}"],
        } for i in range(1, 5)]
        write_json(self.root / "manifests/scenes.json", {"version": 1, "status": "draft", "scenes": scenes})
        write_json(self.root / "manifests/claims.json", {"claims": [{"id": f"c{i}", "text": f"Claim {i}", "source_ids": [f"src{i}"]} for i in range(1, 5)]})
        write_json(self.root / "manifests/sources.json", {"sources": [{"id": f"src{i}", "title": f"Source {i}"} for i in range(1, 5)]})
        write_json(self.root / "manifests/dossier.json", {"title": "Review dossier", "findings": ["finding"]})
        write_json(self.root / "manifests/media_sources.json", {"assets": [
            {"id": "courtroom", "title": "Rechtszaak archief", "type": "image", "mapped_scenes": ["s02", "s03"]},
            {"id": "user-screenshot", "title": "Mijn screenshot", "type": "image", "path": "assets/images/screenshot.png", "mapped_scenes": []},
        ]})
        write_json(self.root / "manifests/clip_sources.json", {"clips": [
            {"intake_id": "cnn-1", "video_title": "CNN interview", "channel": "CNN", "scene_ids": []},
        ]})
        write_json(self.root / "manifests/director_plan.json", {"scenes": [
            {"scene_id": f"s{i:02}", "pacing": "measured", "shots": [{"motion": "slow_zoom"}]} for i in range(1, 5)
        ]})
        write_json(self.root / "manifests/producer_blueprint.json", {"sections": [
            {"id": f"s{i:02}", "role": "hook" if i == 1 else "closing" if i == 4 else "context", "ratios": {"voice_over": .7, "interview": .1, "b_roll": .2}} for i in range(1, 5)
        ]})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reviewable_draft_contains_dossier_and_complete_scene_evidence(self) -> None:
        draft = create_review_draft(self.root)
        scene = draft["scenes"][0]
        self.assertEqual(draft["status"], "reviewable_draft")
        self.assertEqual(draft["dossier"]["title"], "Review dossier")
        self.assertEqual(scene["script"], scene["voice_over_text"])
        self.assertEqual(scene["claims"][0]["id"], "c1")
        self.assertEqual(scene["sources"][0]["id"], "src1")
        self.assertIn("producer", scene["edit_plan"])
        self.assertTrue(scene["camera_direction"])

    def test_natural_intro_revision_targets_only_first_scene(self) -> None:
        before = deepcopy(read_json(self.root / "manifests/scenes.json")["scenes"])
        result = revise_draft(self.root, "Maak de intro spannender.")
        after = read_json(self.root / "manifests/scenes.json")["scenes"]
        self.assertEqual(result["changed_scene_ids"], ["s01"])
        self.assertEqual(after[0]["narrative_tone"], "more_tense")
        self.assertEqual(after[1:], before[1:])

    def test_less_courtroom_media_changes_only_selected_mapping(self) -> None:
        result = revise_draft(self.root, "Gebruik minder beelden van de rechtszaak.", selected_scene_id="s02")
        asset = read_json(self.root / "manifests/media_sources.json")["assets"][0]
        self.assertNotIn("s02", asset["mapped_scenes"])
        self.assertIn("s03", asset["mapped_scenes"])
        self.assertIn("media", result["regenerated_components"])

    def test_add_cnn_interview_maps_existing_clip_to_scene(self) -> None:
        revise_draft(self.root, "Voeg hier het CNN-interview toe.", selected_scene_id="s03")
        clip = read_json(self.root / "manifests/clip_sources.json")["clips"][0]
        self.assertEqual(clip["scene_ids"], ["s03"])

    def test_use_user_screenshot_in_scene_four(self) -> None:
        revise_draft(self.root, "Gebruik mijn screenshot in scène 4.")
        screenshot = read_json(self.root / "manifests/media_sources.json")["assets"][1]
        self.assertEqual(screenshot["mapped_scenes"], ["s04"])

    def test_shorten_scene_changes_duration_and_regeneration_scope(self) -> None:
        result = revise_draft(self.root, "Verkort deze scène met 8 seconden.", selected_scene_id="s02")
        scene = read_json(self.root / "manifests/scenes.json")["scenes"][1]
        self.assertEqual(scene["duration_seconds"], 22.0)
        self.assertEqual(result["regenerated_components"], ["edit", "script", "voice_over"])

    def test_voice_camera_and_outro_requests_map_to_specific_components(self) -> None:
        voice = revise_draft(self.root, "Maak de voice-over emotioneler.", selected_scene_id="s02")
        camera = revise_draft(self.root, "Gebruik meer close-ups.", selected_scene_id="s03")
        outro = revise_draft(self.root, "Laat de outro krachtiger eindigen.")
        scenes = read_json(self.root / "manifests/scenes.json")["scenes"]
        self.assertEqual(scenes[1]["voice_over_delivery"], "more_emotional")
        self.assertEqual(scenes[2]["camera_preference"], "more_close_ups")
        self.assertEqual(scenes[3]["ending_style"], "stronger_resolved_ending")
        self.assertEqual(voice["changed_scene_ids"], ["s02"])
        self.assertEqual(camera["changed_scene_ids"], ["s03"])
        self.assertEqual(outro["changed_scene_ids"], ["s04"])

    def test_approved_scene_is_locked_and_other_scenes_remain_editable(self) -> None:
        approved = approve_scene(self.root, "s01")
        with self.assertRaisesRegex(RuntimeError, "approved and locked"):
            revise_draft(self.root, "Maak de intro spannender.")
        revise_draft(self.root, "Gebruik meer close-ups.", selected_scene_id="s02")
        refreshed = create_review_draft(self.root)
        locked = next(scene for scene in refreshed["scenes"] if scene["id"] == "s01")
        self.assertEqual(locked["approved_fingerprint"], approved["approved_fingerprint"])

    def test_producer_director_critic_review_only_changed_scenes(self) -> None:
        result = revise_draft(self.root, "Gebruik meer close-ups.", selected_scene_id="s03")
        for engine in ("producer", "director", "critic"):
            self.assertEqual(result["evaluations"][engine]["scene_ids"], ["s03"])
            self.assertEqual([item["scene_id"] for item in result["evaluations"][engine]["results"]], ["s03"])

    def test_crash_recovery_rebuilds_review_from_source_manifests(self) -> None:
        create_review_draft(self.root)
        (self.root / "manifests/review_draft.json").unlink()
        recovered = create_review_draft(self.root)
        self.assertEqual(len(recovered["scenes"]), 4)
        self.assertEqual(recovered["scenes"][2]["claims"][0]["id"], "c3")

    def test_revision_history_and_selective_contract_are_idempotent_and_preserving(self) -> None:
        first = revise_draft(self.root, "Gebruik meer close-ups.", selected_scene_id="s03")
        repeated = revise_draft(self.root, "Gebruik meer close-ups.", selected_scene_id="s03")
        draft = create_review_draft(self.root)
        self.assertEqual(repeated["id"], first["id"])
        self.assertTrue(first["unchanged_scenes_preserved"])
        self.assertEqual(len(draft["revision_history"]), 1)
        contract = read_json(self.root / "manifests/selective_regeneration.json")
        self.assertEqual(contract["scene_ids"], ["s03"])
        self.assertTrue(contract["preserve_approved"])

    def test_dashboard_shows_scene_details_and_revision_chat(self) -> None:
        page = DashboardApp(Path.cwd()).draft_review_page_for_root(self.root, "review-case") if hasattr(DashboardApp, "draft_review_page_for_root") else ""
        if not page:
            app = DashboardApp(Path.cwd())
            original = app.project_root
            app.project_root = lambda slug: self.root  # type: ignore[method-assign]
            page = app.draft_review_page("review-case")
            app.project_root = original  # type: ignore[method-assign]
        for label in ("Revisiechat", "Script en voice-over", "Claims en bronnen", "Screenshots en videofragmenten", "Camerarichting en montageplan"):
            self.assertIn(label, page)


if __name__ == "__main__":
    unittest.main()
