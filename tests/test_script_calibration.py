from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.config.settings import load_settings
from inside_case_factory.core.script_calibration import (
    PROTECTED_ARTIFACTS,
    assert_calibration_isolated,
    run_dutch_script_calibration,
    script_stage_maximum_cost,
)


ROOT = Path(__file__).resolve().parents[1]


class ScriptCalibrationTests(unittest.TestCase):
    def test_configured_maximum_cost_is_below_hard_ceiling(self) -> None:
        self.assertLessEqual(script_stage_maximum_cost(load_settings(ROOT)), 0.05)

    def test_calibration_refuses_production_projects_and_existing_output(self) -> None:
        with self.assertRaises(ValueError):
            assert_calibration_isolated(ROOT, ROOT / "projects" / "calibration")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                assert_calibration_isolated(ROOT, Path(tmp))

    def test_calibration_uses_real_write_script_path_and_separate_artifacts(self) -> None:
        words = ["De", "brug", "sluit", "na", "de", "inspectie"] + ["feitelijk"] * 294
        narration = " ".join(words) + "."
        script = {
            "version": 1, "title": "De Oude Havenbrug", "target_duration_minutes": 3,
            "language": "Nederlands", "status": "final", "generated_from": [f"c00{i}" for i in range(1, 7)],
            "opening_hook": "De brug sluit na de inspectie.", "narration": narration,
            "sections": [{"id": "s1", "heading": "Brug", "claim_ids": ["c001"], "beat_ids": ["beat_01", "beat_02", "beat_03"], "text": narration}],
        }

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self):
                return json.dumps({"output_text": json.dumps(script), "usage": {"input_tokens": 500, "output_tokens": 420}}).encode()

        captured = {}
        def fake_urlopen(request, timeout=0):
            captured.update(json.loads(request.data.decode()))
            return Response()

        settings = load_settings(ROOT)
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "new-calibration"
            with patch.dict(os.environ, {"OPENAI_API_KEY": "local-test-key"}):
                with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                    report = run_dutch_script_calibration(settings, output)
            self.assertIn("300-500 words", captured["input"][1]["content"])
            self.assertIn("narration alone must contain at least 300", captured["input"][1]["content"])
            self.assertIn("Never put claim IDs", captured["input"][1]["content"])
            self.assertIn("32 words or fewer", captured["input"][1]["content"])
            self.assertIn("Do not use rhetorical questions", captured["input"][1]["content"])
            self.assertIn("Do not end with a broad lesson", captured["input"][1]["content"])
            self.assertEqual(report["word_count"], 300)
            self.assertTrue((output / "calibration_report.json").exists())
            self.assertTrue((output / "script_candidate.json").exists())
            self.assertFalse(any((output / name).exists() for name in PROTECTED_ARTIFACTS))
            self.assertEqual(report["human_review"]["reviewer_notes"], "")


if __name__ == "__main__":
    unittest.main()
