from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inside_case_factory.core.narrative_quality import analyze_dutch_language


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "dutch_language.json"
ISSUE_FIELDS = (
    "translated_english_patterns",
    "unnatural_phrasing",
    "repeated_sentence_patterns",
    "overdramatic_phrases",
    "spoken_language_issues",
    "long_sentence_count",
    "connector_repetition",
)


def load_language_fixtures() -> list[dict[str, str]]:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(fixtures, list):
        raise ValueError("Dutch language fixtures must be a JSON array.")
    return fixtures


def run_language_fixtures(selection: str = "all") -> list[dict[str, Any]]:
    fixtures = load_language_fixtures()
    selected = fixtures if selection == "all" else [item for item in fixtures if item.get("name") == selection]
    if not selected:
        raise ValueError(f"Unknown language fixture: {selection}")
    results = []
    for fixture in selected:
        analysis = analyze_dutch_language(str(fixture["text"]))
        actual = "pass" if analysis["dutch_language_quality"] == "pass" else "fail"
        expected = str(fixture["expected"])
        results.append({
            "fixture": fixture["name"],
            "expected": expected,
            "actual": actual,
            "detected_issues": {field: analysis[field] for field in ISSUE_FIELDS},
            "rejection_reasons": analysis["language_rejection_reasons"],
            "result": "pass" if actual == expected else "fail",
        })
    return results


def format_language_report(results: list[dict[str, Any]]) -> str:
    blocks = []
    for result in results:
        issues = result["detected_issues"]
        issue_lines = [f"  {field}: {json.dumps(value, ensure_ascii=False)}" for field, value in issues.items()]
        reasons = json.dumps(result["rejection_reasons"], ensure_ascii=False)
        blocks.append("\n".join([
            f"Fixture: {result['fixture']}",
            f"Expected: {result['expected']}",
            f"Actual: {result['actual']}",
            "Detected issues:",
            *issue_lines,
            f"Rejection reasons: {reasons}",
            f"Final: {result['result']}",
        ]))
    return "\n\n".join(blocks)
