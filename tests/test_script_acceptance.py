from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from inside_case_factory.core.narrative_quality import validate_script, validate_story_architecture, validate_architecture_file
from inside_case_factory.core.production import _persist_candidate, _promote_candidate, _write_generation_failure
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.core.project import create_project
from inside_case_factory.providers.reasoning import OpenAIReasoningProvider, ReasoningConfig


class ScriptAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.architecture = {"version": 1, "status": "final", "beats": [{"beat_id": f"beat_{i:02}", "what_happens": "event", "viewer_learns": "fact", "why_here": "order", "curiosity_forward": "next", "claim_ids": [], "high_value_details": []} for i in range(1, 4)], "research_utilization_audit": [], "unused_high_value_details": [], "coverage_gaps": [], "final_reflection": "reflection", "closing_requirements": [], "supplementary_metadata": {}}
        self.claims = [{"id": "c001"}]

    def script(self, words: int, beat_ids: list[str]) -> dict[str, object]:
        return {"narration": "word " * words, "target_duration_minutes": 12, "sections": [{"beat_ids": beat_ids}]}

    def test_986_word_script_fails_for_12_minute_target(self) -> None:
        report = validate_script(self.script(986, ["beat_01", "beat_02", "beat_03"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertTrue(any("te kort" in reason for reason in report["failure_reasons"]))

    def test_missing_story_beats_fail(self) -> None:
        report = validate_script(self.script(1600, ["beat_01"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertIn("beat_02", report["missing_beat_ids"])

    def test_compliant_script_passes(self) -> None:
        report = validate_script(self.script(1600, ["beat_01", "beat_02", "beat_03"]), self.claims, self.architecture)
        self.assertTrue(report["pass"])
        self.assertEqual(report["unused_required_research_details"], [])

    def test_failed_revision_is_not_accepted(self) -> None:
        report = validate_script(self.script(1200, ["beat_01", "beat_02", "beat_03"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertNotEqual(report["failure_reasons"], [])

    def test_quality_report_has_structured_fields(self) -> None:
        report = validate_script(self.script(986, ["beat_01"]), self.claims, self.architecture)
        for field in ("word_count", "estimated_duration_minutes", "missing_beat_ids", "unsupported_claim_ids", "unused_required_research_details", "banned_style_phrases", "repetitive_transitions", "opening_quality", "ending_quality", "failure_reasons"):
            self.assertIn(field, report)

    def test_revision_receives_complete_quality_report(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self):
                return json.dumps({"output_text": json.dumps({"version": 1, "title": "Test", "target_duration_minutes": 12, "language": "English", "status": "final", "generated_from": [], "opening_hook": "Hook", "narration": "word " * 1600, "sections": [{"id": "s1", "heading": "Test", "claim_ids": [], "text": "Test", "beat_ids": ["beat01", "beat02", "beat03"]}]})}).encode()

        captured = {}
        def fake_urlopen(request, timeout=0):
            captured.update(json.loads(request.data.decode()))
            return Response()

        quality = validate_script(self.script(986, ["beat_01", "beat_02", "beat_03"]), self.claims, self.architecture)
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Test")
            provider = OpenAIReasoningProvider(ReasoningConfig(enabled=True, model="gpt-5.5"), api_key="test")
            with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                provider.write_script(project.root, {}, {}, {}, self.claims, 12, "English", quality_report=quality)
        self.assertEqual(captured["input"][1]["content"] if isinstance(captured["input"][1]["content"], str) else "", captured["input"][1]["content"])
        self.assertIn("failure_reasons", json.dumps(captured))

    def test_metadata_cannot_be_a_narrative_beat(self) -> None:
        malformed = {**self.architecture, "beats": [*self.architecture["beats"], "final_reflection"]}
        self.assertFalse(validate_story_architecture(malformed)["valid"])

    def test_malformed_architecture_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_architecture_file(Path(tmp), {"beats": ["coverage_gaps"]})
            self.assertFalse(report["valid"])
            self.assertTrue((Path(tmp) / "manifests/story_architecture_validation_report.json").exists())

    def test_only_genuine_ids_required_and_unknown_fails(self) -> None:
        report = validate_script(self.script(1600, ["beat_01", "beat_02", "beat_03", "final_reflection"]), self.claims, self.architecture)
        self.assertEqual(report["missing_beat_ids"], [])
        self.assertEqual(report["unknown_beat_ids"], ["final_reflection"])
        self.assertFalse(report["pass"])

    def test_candidate_history_promotion_and_failure_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "manifests").mkdir()
            accepted = self.script(1600, ["beat_01", "beat_02", "beat_03"])
            good = validate_script(accepted, self.claims, self.architecture)
            _persist_candidate(root, 1, accepted, good); _promote_candidate(root, 1, accepted, good)
            old_script = read_json(root / "manifests/script.json"); old_report = read_json(root / "manifests/script_quality_report.json")
            bad = self.script(10, ["beat_99"]); bad_report = validate_script(bad, self.claims, self.architecture)
            _persist_candidate(root, 2, bad, bad_report); _write_generation_failure(root, [(1, bad_report), (2, bad_report)], True)
            self.assertTrue((root / "manifests/script_candidate_1_quality_report.json").exists())
            self.assertTrue((root / "manifests/script_candidate_2_quality_report.json").exists())
            self.assertEqual(read_json(root / "manifests/script.json"), old_script)
            self.assertEqual(read_json(root / "manifests/script_quality_report.json"), old_report)
            self.assertEqual(old_script["accepted_candidate_id"], old_report["accepted_candidate_id"])
            failure = read_json(root / "manifests/script_generation_failure.json")
            self.assertEqual(len(failure["candidates"]), 2)
            self.assertTrue(all("word_count" in item and "unknown_beat_ids" in item for item in failure["candidates"]))


if __name__ == "__main__":
    unittest.main()
