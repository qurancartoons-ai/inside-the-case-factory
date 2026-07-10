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
from inside_case_factory.utils.files import read_json


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
            self.assertTrue((output / "script_candidate_1.json").exists())
            self.assertFalse(any((output / name).exists() for name in PROTECTED_ARTIFACTS))
            self.assertEqual(report["human_review"]["reviewer_notes"], "")

    def test_calibration_retries_only_validator_failures_and_stops_on_pass(self) -> None:
        base = {
            "version": 1, "title": "De Oude Havenbrug", "target_duration_minutes": 3,
            "language": "Nederlands", "status": "final", "generated_from": ["c001"],
            "opening_hook": "De brug sluit.",
            "sections": [{"id": "s1", "heading": "Brug", "claim_ids": ["c001"],
                          "beat_ids": ["beat_01", "beat_02", "beat_03"], "text": "tekst"}],
        }
        bad = {**base, "narration": "Te kort."}
        starts = "Vandaag Daarna Vervolgens Intussen Tegelijk Later Eerst Ook Hierdoor Daarom Bovendien Inmiddels Uiteindelijk Vervolgensertijd Aansluitend Nadien Verder Daarbij Daarnaast Vervolgensaanvullend Daarnaopnieuw Vervolgenslater Intussenook Tegelijkook Laterook Eerstook Hierdoorook Daaromook Inmiddelsook Uiteindelijkook".split()
        good_text = " ".join(
            f"{start} blijft de brug tijdens gepland onderhoud veilig open voor verkeer."
            for start in starts
        )
        good = {**base, "narration": good_text,
                "sections": [{**base["sections"][0], "text": good_text}]}

        def response(script, tokens):
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return None
                def read(self):
                    return json.dumps({"output_text": json.dumps(script), "usage": {
                        "input_tokens": tokens, "output_tokens": tokens,
                    }}).encode()
            return Response()

        requests = []
        def fake_urlopen(request, timeout=0):
            requests.append(json.loads(request.data.decode()))
            return response(bad if len(requests) == 1 else good, 100)

        settings = load_settings(ROOT)
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "retry-calibration"
            with patch.dict(os.environ, {"OPENAI_API_KEY": "local-test-key"}):
                with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                    report = run_dutch_script_calibration(settings, output, maximum_attempts=3)
            self.assertEqual(report["attempts_used"], 2)
            self.assertFalse(report["attempts"][0]["overall_acceptance"])
            self.assertTrue(report["attempts"][1]["overall_acceptance"])
            self.assertNotIn('"dossier"', requests[0]["input"][1]["content"])
            self.assertNotIn('"research_plan"', requests[0]["input"][1]["content"])
            self.assertIn("repair_plan", requests[1]["input"][1]["content"])
            self.assertIn("existing_script", requests[1]["input"][1]["content"])
            self.assertNotIn('"dossier"', requests[1]["input"][1]["content"])
            self.assertIn("Change only passages", requests[1]["input"][1]["content"])
            self.assertTrue((output / "best_valid_script_candidate.json").exists())
            self.assertEqual(len(read_json(output / "manifests/reasoning_usage.json")["calls"]), 2)

    def test_three_rejected_calibration_candidates_never_create_final_artifact(self) -> None:
        rejected = {
            "version": 1, "title": "Brug", "target_duration_minutes": 3, "language": "Nederlands",
            "status": "final", "generated_from": ["c001"], "opening_hook": "Kort.",
            "narration": "Te kort.",
            "sections": [{"id": "s1", "heading": "Brug", "claim_ids": ["c001"],
                          "beat_ids": ["beat_01", "beat_02", "beat_03"], "text": "Te kort."}],
        }

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self):
                return json.dumps({"output_text": json.dumps(rejected), "usage": {
                    "input_tokens": 10, "output_tokens": 10,
                }}).encode()

        settings = load_settings(ROOT)
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "failed-calibration"
            with patch.dict(os.environ, {"OPENAI_API_KEY": "local-test-key"}):
                with patch("inside_case_factory.providers.reasoning.urlopen", return_value=Response()) as api:
                    report = run_dutch_script_calibration(settings, output, maximum_attempts=3)
            self.assertEqual(api.call_count, 3)
            self.assertEqual(report["attempts_used"], 3)
            self.assertFalse(report["overall_acceptance"])
            self.assertFalse((output / "best_valid_script_candidate.json").exists())
            self.assertTrue((output / "script_candidate_3_quality_report.json").exists())

    def test_calibration_attempt_limit_and_budget_are_bounded(self) -> None:
        settings = load_settings(ROOT)
        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(ValueError):
                run_dutch_script_calibration(settings, Path(parent) / "too-many", maximum_attempts=4)


if __name__ == "__main__":
    unittest.main()
