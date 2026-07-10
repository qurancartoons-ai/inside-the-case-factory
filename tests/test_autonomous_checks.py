from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

from inside_case_factory.core.autonomous_checks import (
    FULL_TEST_COMMAND, HEALTH_COMMAND, LANGUAGE_COMMAND, needs_language_check,
    run_autonomous_checks, verification_commands,
)


class AutonomousChecksTests(unittest.TestCase):
    def test_required_commands_are_exact(self) -> None:
        self.assertEqual(FULL_TEST_COMMAND, ("python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"))
        self.assertEqual(HEALTH_COMMAND, ("python3", "-m", "inside_case_factory", "health"))
        self.assertEqual(LANGUAGE_COMMAND, ("python3", "-m", "inside_case_factory", "language-check", "--fixture", "all"))

    def test_relevant_changes_enable_language_check(self) -> None:
        for path in (
            "inside_case_factory/providers/reasoning.py", "inside_case_factory/core/narrative_quality.py",
            "tests/test_script_acceptance.py", "inside_case_factory/fixtures/dutch_language.json",
        ):
            self.assertTrue(needs_language_check([path]), path)
        self.assertFalse(needs_language_check(["inside_case_factory/core/media.py"]))

    def test_command_sequence_includes_optional_language_check(self) -> None:
        self.assertEqual(verification_commands(False), [FULL_TEST_COMMAND, HEALTH_COMMAND])
        self.assertEqual(verification_commands(True), [FULL_TEST_COMMAND, HEALTH_COMMAND, LANGUAGE_COMMAND])

    def test_runner_stops_on_failure_and_returns_exact_exit_code(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 7 if command == HEALTH_COMMAND else 0)

        self.assertEqual(run_autonomous_checks(Path("."), "always", runner=runner), 7)
        self.assertEqual(calls, [FULL_TEST_COMMAND, HEALTH_COMMAND])

    def test_runner_completes_all_requested_checks(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        self.assertEqual(run_autonomous_checks(Path("."), "always", runner=runner), 0)
        self.assertEqual(calls, [FULL_TEST_COMMAND, HEALTH_COMMAND, LANGUAGE_COMMAND])


if __name__ == "__main__":
    unittest.main()
