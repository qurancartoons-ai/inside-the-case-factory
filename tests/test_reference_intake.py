from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.project import create_project
from inside_case_factory.core.reference_intake import (
    apply_reference_to_script,
    create_reference_intake,
    match_transcript,
    parse_time_range,
    select_reference_match,
    validate_reference_safety,
    youtube_video_id,
)
from inside_case_factory.utils.files import read_json
from inside_case_factory.web.dashboard import DashboardApp


TRANSCRIPT = [
    {"start": 0, "duration": 8, "text": "Welkom bij deze uitzending"},
    {"start": 72, "duration": 6, "text": "De minister zegt dat het rapport gisteren is ontvangen", "speaker": "M. de Vries"},
    {"start": 78, "duration": 7, "text": "Daarna is direct een onderzoek gestart", "speaker": "M. de Vries"},
    {"start": 900, "duration": 10, "text": "Bedankt voor het kijken"},
]


class ReferenceIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = create_project(Path(self.temporary.name), "Interviewonderzoek")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def intake(self, **overrides):
        options = {
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "note": "Gebruik de passage over het ontvangen rapport",
            "visible_text": "rapport gisteren ontvangen",
            "metadata": {"title": "Nieuwsuur interview", "channel": "Nieuwsuur", "speaker": "M. de Vries"},
            "transcript": TRANSCRIPT,
        }
        options.update(overrides)
        return create_reference_intake(self.project.root, **options)

    def test_screenshot_recognition_uses_visible_subtitle_text(self) -> None:
        screenshot = Path(self.temporary.name) / "frame.png"
        screenshot.write_bytes(b"fixture")
        result = self.intake(source_url="", local_path=screenshot, original_filename="frame.png")
        self.assertEqual(result["input_kind"], "screenshot")
        self.assertEqual(result["reference_intent"]["start_seconds"], 71.0)
        self.assertEqual(result["reference_intent"]["speaker"], "M. de Vries")

    def test_screenshot_analyzer_adapter_supplies_ocr_and_hypotheses(self) -> None:
        screenshot = Path(self.temporary.name) / "ocr-frame.jpg"
        screenshot.write_bytes(b"fixture")
        analyzer = lambda _: {"visible_text": "rapport gisteren ontvangen", "metadata": {"people": ["M. de Vries"]}}
        result = self.intake(source_url="", local_path=screenshot, visible_text="", screenshot_analyzer=analyzer)
        self.assertEqual(result["reference_intent"]["start_seconds"], 71.0)
        self.assertEqual(result["reference_intent"]["hypotheses"]["people"], ["M. de Vries"])

    def test_source_resolver_adapter_reads_metadata_chapters_and_transcript(self) -> None:
        resolver = lambda _: {
            "metadata": {"title": "Resolved interview", "channel": "Resolved channel"},
            "transcript": TRANSCRIPT,
        }
        result = create_reference_intake(
            self.project.root, source_url="https://youtu.be/resolved", visible_text="rapport gisteren ontvangen",
            source_resolver=resolver,
        )
        self.assertEqual(result["reference_intent"]["video_title"], "Resolved interview")
        self.assertEqual(result["reference_intent"]["start_seconds"], 71.0)

    def test_transcript_matching_ranks_semantic_token_overlap(self) -> None:
        matches = match_transcript("rapport gisteren ontvangen", TRANSCRIPT)
        self.assertGreater(matches[0]["confidence"], 0.8)
        self.assertIn("rapport", matches[0]["text"])

    def test_timestamp_selection_overrides_match_and_parses_range(self) -> None:
        result = self.intake(timestamp="01:15-01:29")
        intent = result["reference_intent"]
        self.assertEqual((intent["start_seconds"], intent["end_seconds"]), (75.0, 89.0))
        self.assertEqual(parse_time_range("1:02:03"), (3723.0, None))

    def test_multiple_possible_sources_have_confidence_scores(self) -> None:
        result = self.intake(possible_sources=[
            {"url": "https://youtu.be/other", "title": "Andere upload", "channel": "Archief", "confidence": .7, "transcript": TRANSCRIPT},
            {"url": "https://youtu.be/third", "title": "Kort fragment", "channel": "Nieuws", "confidence": .5, "transcript": TRANSCRIPT},
        ])
        alternatives = result["reference_intent"]["alternative_matches"]
        self.assertGreaterEqual(len(alternatives), 2)
        self.assertTrue(all("confidence" in match for match in alternatives))

    def test_local_clip_is_copied_as_offline_fallback(self) -> None:
        clip = Path(self.temporary.name) / "interview.mp4"
        clip.write_bytes(b"local-video-fixture")
        result = self.intake(source_url="", local_path=clip, original_filename=clip.name)
        stored = self.project.root / result["stored_path"]
        self.assertEqual(stored.read_bytes(), b"local-video-fixture")
        self.assertEqual(result["input_kind"], "local_media")

    def test_youtube_url_variants_are_recognized_without_network(self) -> None:
        self.assertEqual(youtube_video_id("https://youtu.be/abc123?t=7"), "abc123")
        self.assertEqual(youtube_video_id("https://www.youtube.com/shorts/xyz"), "xyz")

    def test_selection_integrates_research_and_script_with_attribution(self) -> None:
        result = self.intake()
        select_reference_match(self.project.root, result["id"], why_relevant="Kernverklaring", user_selected_for_edit=True)
        research = read_json(self.project.root / "manifests/reference_research.json")
        script = read_json(self.project.root / "manifests/reference_script_integration.json")
        self.assertEqual(research["interview_statement"]["epistemic_status"], "speaker_statement_not_verified_fact")
        self.assertEqual(script["clip"]["attribution"], "speaker_statement")
        self.assertIn("niet op zichzelf een bewezen feit", script["before_clip"])

        generated = apply_reference_to_script(self.project.root, {"narration": "Basis", "sections": []})
        self.assertEqual(generated["reference_interviews"][0]["clip"]["speaker"], "M. de Vries")
        self.assertIn("Corroborate", generated["research_directions"][0])

    def test_edit_plan_has_j_cut_l_cut_subtitles_and_context(self) -> None:
        result = self.intake()
        select_reference_match(self.project.root, result["id"], user_selected_for_edit=True)
        plan = read_json(self.project.root / "manifests/reference_edit_plan.json")
        self.assertGreater(plan["audio"]["j_cut_seconds"], 0)
        self.assertGreater(plan["audio"]["l_cut_seconds"], 0)
        self.assertTrue(plan["subtitles"]["speaker_labels"])
        self.assertTrue(plan["context_card"]["enabled"])
        self.assertTrue(plan["audio"]["preserve_intelligibility"])

    def test_source_manifest_preserves_provenance_and_duration(self) -> None:
        result = self.intake(timestamp="1:12-1:26")
        select_reference_match(self.project.root, result["id"], why_relevant="Projectcontext", user_selected_for_edit=True)
        source = read_json(self.project.root / "manifests/clip_sources.json")["clips"][0]
        self.assertEqual(source["channel"], "Nieuwsuur")
        self.assertEqual(source["video_title"], "Nieuwsuur interview")
        self.assertEqual(source["used_duration_seconds"], 14.0)
        self.assertEqual(source["project_context"], "Projectcontext")

    def test_rights_unknown_does_not_block_explicit_user_selection(self) -> None:
        result = self.intake()
        select_reference_match(self.project.root, result["id"], user_selected_for_edit=True)
        plan = read_json(self.project.root / "manifests/reference_edit_plan.json")
        self.assertFalse(plan["rights"]["blocks_edit"])
        self.assertEqual(plan["rights"]["decision_maker"], "user")

    def test_content_id_evasion_is_explicitly_forbidden(self) -> None:
        result = self.intake()
        select_reference_match(self.project.root, result["id"], user_selected_for_edit=True)
        self.assertEqual(validate_reference_safety(self.project.root), [])
        plan = read_json(self.project.root / "manifests/reference_edit_plan.json")
        self.assertFalse(plan["safety"]["content_id_evasion"])
        self.assertFalse(plan["safety"]["alter_words"])

    def test_intake_and_integration_are_idempotent_for_crash_recovery(self) -> None:
        first = self.intake()
        second = self.intake()
        self.assertEqual(first["id"], second["id"])
        select_reference_match(self.project.root, first["id"], user_selected_for_edit=True)
        select_reference_match(self.project.root, first["id"], user_selected_for_edit=True)
        clips = read_json(self.project.root / "manifests/clip_sources.json")["clips"]
        self.assertEqual(len(clips), 1)

    def test_unconfirmed_match_does_not_enter_edit_plan(self) -> None:
        result = self.intake()
        select_reference_match(self.project.root, result["id"], user_selected_for_edit=False)
        self.assertFalse((self.project.root / "manifests/reference_edit_plan.json").exists())

    def test_dashboard_exposes_upload_link_review_and_explicit_checkbox(self) -> None:
        app = DashboardApp(Path.cwd())
        dashboard_project = create_project(Path(self.temporary.name), "Dashboard intake", "dashboard-intake")
        create_reference_intake(dashboard_project.root, source_url="https://youtu.be/abc", note="test")
        with patch.object(app, "project_root", return_value=dashboard_project.root):
            page = app.reference_intake_page("dashboard-intake")
        self.assertIn('type="file"', page)
        self.assertIn("YouTube-URL", page)
        self.assertIn("Door gebruiker geselecteerd voor montage", page)
        self.assertIn("Gevonden fragment controleren", page)


if __name__ == "__main__":
    unittest.main()
