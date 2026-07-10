from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from inside_case_factory.core.narrative_quality import validate_script
from inside_case_factory.core.project import create_project
from inside_case_factory.providers.reasoning import OpenAIReasoningProvider, ReasoningConfig


class ScriptAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.architecture = {"beats": [{"id": f"beat{i:02}"} for i in range(1, 4)], "research_utilization_audit": [], "unused_high_value_details": []}
        self.claims = [{"id": "c001"}]

    def script(self, words: int, beat_ids: list[str]) -> dict[str, object]:
        return {"narration": "word " * words, "target_duration_minutes": 12, "sections": [{"beat_ids": beat_ids}]}

    def test_986_word_script_fails_for_12_minute_target(self) -> None:
        report = validate_script(self.script(986, ["beat01", "beat02", "beat03"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertTrue(any("te kort" in reason for reason in report["failure_reasons"]))

    def test_missing_story_beats_fail(self) -> None:
        report = validate_script(self.script(1600, ["beat01"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertIn("beat02", report["missing_beat_ids"])

    def test_compliant_script_passes(self) -> None:
        report = validate_script(self.script(1600, ["beat01", "beat02", "beat03"]), self.claims, self.architecture)
        self.assertTrue(report["pass"])
        self.assertEqual(report["unused_required_research_details"], [])

    def test_failed_revision_is_not_accepted(self) -> None:
        report = validate_script(self.script(1200, ["beat01", "beat02", "beat03"]), self.claims, self.architecture)
        self.assertFalse(report["pass"])
        self.assertNotEqual(report["failure_reasons"], [])

    def test_quality_report_has_structured_fields(self) -> None:
        report = validate_script(self.script(986, ["beat01"]), self.claims, self.architecture)
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

        quality = validate_script(self.script(986, ["beat01", "beat02", "beat03"]), self.claims, self.architecture)
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Test")
            provider = OpenAIReasoningProvider(ReasoningConfig(enabled=True, model="gpt-5.5"), api_key="test")
            with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                provider.write_script(project.root, {}, {}, {}, self.claims, 12, "English", quality_report=quality)
        self.assertEqual(captured["input"][1]["content"] if isinstance(captured["input"][1]["content"], str) else "", captured["input"][1]["content"])
        self.assertIn("failure_reasons", json.dumps(captured))


if __name__ == "__main__":
    unittest.main()
