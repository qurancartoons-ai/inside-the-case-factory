from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Sequence


Command = tuple[str, ...]
FULL_TEST_COMMAND: Command = ("python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")
HEALTH_COMMAND: Command = ("python3", "-m", "inside_case_factory", "health")
LANGUAGE_COMMAND: Command = ("python3", "-m", "inside_case_factory", "language-check", "--fixture", "all")
LANGUAGE_RELEVANT_PARTS = ("script", "narrative_quality", "language_check", "dutch_language", "reasoning.py")


def changed_paths(root: Path) -> list[str]:
    commands = (("git", "diff", "--name-only", "HEAD"), ("git", "ls-files", "--others", "--exclude-standard"))
    paths: set[str] = set()
    for command in commands:
        result = subprocess.run(command, cwd=root, check=False, text=True, capture_output=True)
        if result.returncode == 0:
            paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(paths)


def needs_language_check(paths: Sequence[str]) -> bool:
    return any(any(part in path.casefold() for part in LANGUAGE_RELEVANT_PARTS) for path in paths)


def verification_commands(include_language: bool) -> list[Command]:
    commands = [FULL_TEST_COMMAND, HEALTH_COMMAND]
    if include_language:
        commands.append(LANGUAGE_COMMAND)
    return commands


def run_autonomous_checks(
    root: Path,
    language_mode: str = "auto",
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    include_language = language_mode == "always" or (language_mode == "auto" and needs_language_check(changed_paths(root)))
    commands = verification_commands(include_language)
    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {' '.join(command)}", flush=True)
        result = runner(command, cwd=root, check=False)
        if result.returncode:
            print(f"FAILED ({result.returncode}): {' '.join(command)}", flush=True)
            return int(result.returncode)
    print("All required offline checks passed.", flush=True)
    return 0
