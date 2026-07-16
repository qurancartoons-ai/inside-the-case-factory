from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.draft_review import approve_scene, create_review_draft, revise_draft
from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import (
    PHASES, apply_dossier_instruction, production_progress, revision_change_plan,
    supported_script_map, youtube_draft,
)
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


class DashboardExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "UX Case").root
        write_json(self.root / "manifests/sources.json", {"sources": [{"id": "src1", "title": "Source", "url": "https://example.invalid", "review_status": "approved"}]})
        write_json(self.root / "manifests/claims.json", {"claims": [{"id": "c1", "text": "Supported fact", "source_ids": ["src1"], "review_status": "approved"}]})
        write_json(self.root / "manifests/scenes.json", {"scenes": [
            {"id": "s01", "index": 1, "heading": "Intro", "narration": "First sentence. Second sentence.", "duration_seconds": 20, "claim_ids": ["c1"]},
            {"id": "s02", "index": 2, "heading": "Outro", "narration": "Closing.", "duration_seconds": 10, "claim_ids": ["c1"]},
        ]})
        write_json(self.root / "manifests/dossier.json", {"summary": "Dossier"})
        write_json(self.root / "manifests/media_sources.json", {"assets": []})
        create_review_draft(self.root)
        self.app = DashboardApp(Path.cwd())
        self.app.project_root = lambda slug: self.root  # type: ignore[method-assign]

    def tearDown(self):
        self.temporary.cleanup()

    def test_project_wizard_contains_every_required_input(self):
        page = self.app.new_project_wizard()
        for name in ("prompt", "duration", "language", "style", "audience", "screenshot", "clip", "youtube_urls", "dossier", "provider_profile", "budget", "mode"):
            self.assertIn(f'name="{name}"', page)

    def test_live_progress_has_all_eleven_phases_and_costs(self):
        result = production_progress(self.root)
        self.assertEqual(tuple(phase["name"] for phase in result["phases"]), PHASES)
        self.assertTrue(all("provider" in phase and "estimated_cost_usd" in phase and "artifacts" in phase for phase in result["phases"]))

    def test_progress_recovers_safely_from_interrupted_state(self):
        write_json(self.root / "manifests/orchestration.json", {"status": "interrupted", "last_error": "render crashed"})
        result = production_progress(self.root)
        self.assertIn("render crashed", result["blockers"])
        self.assertEqual(len(result["phases"]), 11)

    def test_dossier_natural_actions_are_idempotent(self):
        apply_dossier_instruction(self.root, "onderzoek dit verder")
        apply_dossier_instruction(self.root, "onderzoek dit verder")
        queue = read_json(self.root / "manifests/research_followups.json")
        self.assertEqual(len(queue["items"]), 1)
        apply_dossier_instruction(self.root, "gebruik deze bron niet", item_id="src1")
        self.assertEqual(read_json(self.root / "manifests/sources.json")["sources"][0]["review_status"], "rejected")

    def test_script_claim_support_map_is_visible(self):
        mapping = supported_script_map(self.root)
        self.assertEqual(mapping[0]["claims"][0]["id"], "c1")
        self.assertIn("Supported fact", self.app.dossier_review_page("ux-case"))

    def test_revision_chat_creates_plan_before_execution(self):
        plan = revision_change_plan(self.root, "maak de intro spannender")
        self.assertEqual(plan["status"], "awaiting_confirmation")
        self.assertEqual(plan["scene_ids"], ["s01"])
        self.assertFalse(read_json(self.root / "manifests/scenes.json")["scenes"][0].get("narrative_tone"))

    def test_partial_revision_changes_only_target_scene(self):
        before = read_json(self.root / "manifests/scenes.json")["scenes"][1]
        revise_draft(self.root, "maak de intro spannender")
        after = read_json(self.root / "manifests/scenes.json")["scenes"]
        self.assertEqual(after[1], before)
        self.assertEqual(read_json(self.root / "manifests/selective_regeneration.json")["scene_ids"], ["s01"])

    def test_natural_revision_variants_route_components(self):
        cases = (("vervang dit beeld", "media"), ("haal deze zin weg", "script"), ("voeg meer context toe", "script"), ("maak de voice-over warmer", "voice_over"), ("laat het interview eerder beginnen", "clips"))
        for command, component in cases:
            plan = revision_change_plan(self.root, command, "s01")
            self.assertIn(component, plan["components"])

    def test_video_preview_and_scene_thumbnails_are_rendered_in_review_page(self):
        page = self.app.draft_review_page("ux-case")
        self.assertIn("<video", page)
        self.assertIn("/preview/video", page)
        self.assertIn("/preview/thumbnail/s01", page)

    def test_youtube_export_is_private_and_never_auto_confirmed(self):
        draft = youtube_draft(self.root)
        self.assertEqual(draft["privacy_status"], "private")
        self.assertFalse(draft["upload_confirmed"])
        page = self.app.youtube_draft_page("ux-case")
        self.assertIn("expliciete bevestiging", page)

    def test_scene_approval_is_preserved_during_other_revision(self):
        approved = approve_scene(self.root, "s01")
        revise_draft(self.root, "maak de outro krachtiger")
        scene = next(item for item in create_review_draft(self.root)["scenes"] if item["id"] == "s01")
        self.assertEqual(scene["approved_fingerprint"], approved["approved_fingerprint"])

    def test_dashboard_layout_has_mobile_viewport_reflow_and_touch_targets(self):
        page = self.app.page("Mobile", "<p>content</p>")
        self.assertIn('name="viewport"', page)
        self.assertIn("max-width: 620px", page)
        self.assertIn("min-height:44px", page)
        self.assertIn(".review-player", page)

    def test_project_home_exposes_all_review_destinations(self):
        page = self.app.project_detail("ux-case")
        for path in ("/production", "/dossier-review", "/draft-review", "/youtube-draft"):
            self.assertIn(path, page)


if __name__ == "__main__":
    unittest.main()
