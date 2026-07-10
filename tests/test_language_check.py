from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from inside_case_factory.cli.main import main
from inside_case_factory.core.language_check import load_language_fixtures, run_language_fixtures


class DutchLanguageCheckTests(unittest.TestCase):
    def test_fixture_set_covers_required_cases(self) -> None:
        fixtures = load_language_fixtures()
        self.assertEqual(
            {fixture["name"] for fixture in fixtures},
            {
                "natural_professional_dutch", "translated_english_dutch",
                "generic_ai_suspense_cliches", "repetitive_paragraph_openings",
                "repeated_rhetorical_questions", "overly_long_spoken_sentence",
                "bureaucratic_abstract_wording", "restrained_factual_narration",
            },
        )

    def test_all_fixture_expectations_match(self) -> None:
        results = run_language_fixtures("all")
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result["result"] == "pass" for result in results))
        self.assertTrue(all("rejection_reasons" in result for result in results))

    def test_command_prints_human_readable_report_and_succeeds(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["language-check", "--fixture", "all"])
        report = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Fixture: natural_professional_dutch", report)
        self.assertIn("Detected issues:", report)
        self.assertIn("Rejection reasons:", report)
        self.assertIn("Final: pass", report)

    def test_single_fixture_can_be_selected(self) -> None:
        results = run_language_fixtures("translated_english_dutch")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["actual"], "fail")
        self.assertTrue(results[0]["rejection_reasons"])

    def test_unknown_fixture_exits_nonzero(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = main(["language-check", "--fixture", "missing"])
        self.assertEqual(status, 2)
        self.assertIn("Unknown language fixture", errors.getvalue())

    def test_command_exits_nonzero_when_expectation_does_not_match(self) -> None:
        mismatch = [{
            "fixture": "forced_mismatch", "expected": "pass", "actual": "fail",
            "detected_issues": {}, "rejection_reasons": ["forced"], "result": "fail",
        }]
        with patch("inside_case_factory.cli.main.run_language_fixtures", return_value=mismatch):
            with redirect_stdout(io.StringIO()):
                status = main(["language-check", "--fixture", "all"])
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
