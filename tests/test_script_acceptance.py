from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from inside_case_factory.core.narrative_quality import validate_script, validate_story_architecture, validate_architecture_file
from inside_case_factory.core.production import (
    _generate_validated_script_candidates,
    _persist_candidate,
    _promote_candidate,
    _write_generation_failure,
)
from inside_case_factory.core.script_repair import build_script_repair_plan
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.core.project import create_project
from inside_case_factory.providers.reasoning import OpenAIReasoningProvider, ReasoningConfig, ReasoningProviderError


class ScriptAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.architecture = {"version": 1, "status": "final", "beats": [{"beat_id": f"beat_{i:02}", "what_happens": "event", "viewer_learns": "fact", "why_here": "order", "curiosity_forward": "next", "claim_ids": [], "high_value_details": []} for i in range(1, 4)], "research_utilization_audit": [], "unused_high_value_details": [], "coverage_gaps": [], "final_reflection": "reflection", "closing_requirements": [], "supplementary_metadata": {}}
        self.claims = [{"id": "c001", "text": "Goedgekeurde testfeiten: 1998, 2001, 2004, 2012, twee, drie, acht en zestig.", "date": "2012-03-12"}]

    def script(self, words: int, beat_ids: list[str]) -> dict[str, object]:
        return {"narration": "word " * words, "target_duration_minutes": 12, "sections": [{"beat_ids": beat_ids}]}

    def language_report(self, narration: str, language: str = "Nederlands") -> dict[str, object]:
        script = {"narration": narration, "language": language, "target_duration_minutes": 1, "sections": [{"beat_ids": ["beat_01", "beat_02", "beat_03"]}]}
        config = {"minimum_words": 1, "maximum_words": 1000, "words_per_minute": 125, "duration_tolerance": 10}
        return validate_script(script, self.claims, self.architecture, config)

    def test_natural_dutch_narration_passes(self) -> None:
        text = "In 1998 opent de gemeente het nieuwe station. Reizigers krijgen een rechtstreekse verbinding met de stad.\n\nDrie jaar later rijden er dagelijks zestig treinen. Dat blijkt uit de dienstregeling van dat jaar.\n\nDe verbouwing begint in mei 2004. Het oude perron blijft tijdens het werk in gebruik."
        report = self.language_report(text)
        self.assertTrue(report["pass"])
        self.assertEqual(report["dutch_language_quality"], "pass")

    def test_translated_english_dutch_fails(self) -> None:
        report = self.language_report("De onderzoeker maakte zijn weg naar het gebouw. Aan het einde van de dag was het dossier compleet.")
        self.assertFalse(report["pass"])
        self.assertIn("maakte zijn/haar/hun weg naar", report["translated_english_patterns"])

    def test_unnatural_dutch_and_split_compound_number_fail(self) -> None:
        report = self.language_report("Na deze schrapping verving de aannemer zestig vier verbindingen om zo vroeg mogelijke schade te vinden.")
        self.assertFalse(report["pass"])
        self.assertIn("na deze schrapping", report["unnatural_phrasing"])
        self.assertIn("incorrectly_spaced_compound_number", report["unnatural_phrasing"])
        self.assertIn("Unnatural or ungrammatical Dutch phrasing is present.", report["language_rejection_reasons"])

    def test_repeated_cliche_transitions_fail(self) -> None:
        text = "Maar achter de schermen liep het onderzoek door.\n\nMaar achter de schermen zocht de politie verder."
        report = self.language_report(text)
        self.assertFalse(report["pass"])
        self.assertIn("maar achter de schermen", report["overdramatic_phrases"])
        self.assertIn("maar", report["connector_repetition"])

    def test_overdramatic_language_fails(self) -> None:
        report = self.language_report("Niemand kon vermoeden dat een duister geheim de waarheid zou veranderen.")
        self.assertFalse(report["pass"])
        self.assertIn("niemand kon vermoeden", report["overdramatic_phrases"])
        self.assertIn("een duister geheim", report["overdramatic_phrases"])

    def test_generic_ai_conclusion_and_awkward_wording_fail(self) -> None:
        report = self.language_report("Die kwetsuur markeerde het begin van een complexe periode. Het herstel toont aan dat techniek hand in hand gaat met vertrouwen en een nieuwe standaard schept.")
        self.assertFalse(report["pass"])
        self.assertIn("die kwetsuur", report["unnatural_phrasing"])
        self.assertIn("toont aan dat", report["overdramatic_phrases"])
        self.assertIn("hand in hand", report["overdramatic_phrases"])

    def test_calibration_style_turning_point_and_visible_safety_fail(self) -> None:
        report = self.language_report("Deze heropening markeert een keerpunt. Vanaf nu is de veiligheid ook zichtbaar geborgd.")
        self.assertFalse(report["pass"])
        self.assertIn("markeert een keerpunt", report["overdramatic_phrases"])
        self.assertIn("zichtbaar geborgd", report["unnatural_phrasing"])

    def test_dutch_spoken_year_must_be_supported_by_claims(self) -> None:
        self.claims = [{"id": "c001", "date": "2020-03-02", "text": "Het herstel begon in 2020."}]
        report = self.language_report("Het herstel begint in tweeduizendtachtig. De brug opent in tweeduizendtweeentwintig.")
        self.assertFalse(report["pass"])
        self.assertEqual(report["unsupported_narrated_years"], [2022, 2080])
        self.assertIn("Narration contains years not supported by approved claims.", report["failure_reasons"])

    def test_wrong_number_and_new_name_are_factually_locked(self) -> None:
        self.claims = [{"id": "c001", "text": "De brug in Utrecht kreeg twaalf sensoren."}]
        report = self.language_report("De brug in Rotterdam kreeg dertien sensoren.")
        self.assertFalse(report["pass"])
        self.assertEqual(report["unsupported_narrated_numbers"], [13])
        self.assertEqual(report["unsupported_narrated_names"], ["Rotterdam"])
        self.assertEqual({item["category"] for item in report["factual_lock_violations"]}, {"unsupported_name", "unsupported_number"})

    def test_critic_plan_names_exact_passage_and_forbids_new_facts(self) -> None:
        self.claims = [{"id": "c001", "text": "Het herstel begon in 2020.", "date": "2020-01-01"}]
        script = {"narration": "Het herstel begint in tweeduizendtachtig."}
        report = self.language_report(script["narration"])
        plan = build_script_repair_plan(script, report)
        issue = next(item for item in plan["issues"] if item["category"] == "unsupported_year")
        self.assertEqual(issue["value"], 2080)
        self.assertEqual(issue["passages"], [script["narration"]])
        self.assertTrue(any("Do not introduce facts" in rule for rule in plan["constraints"]))

    def test_any_rhetorical_question_and_hand_in_hand_cliche_fail(self) -> None:
        report = self.language_report("Wat veroorzaakte deze schade eigenlijk? Daarna gaan veiligheid en openheid hand in hand.")
        self.assertFalse(report["pass"])
        self.assertIn("rhetorical questions are present.", report["language_rejection_reasons"])
        self.assertIn("hand in hand", report["overdramatic_phrases"])

    def test_repeated_rhetorical_questions_fail(self) -> None:
        report = self.language_report("Maar waarom zweeg hij? De politie onderzocht de brief. Maar waarom zweeg hij?")
        self.assertFalse(report["pass"])
        self.assertTrue(report["repeated_sentence_patterns"])
        self.assertTrue(any("rhetorical" in reason for reason in report["language_rejection_reasons"]))

    def test_long_awkward_spoken_sentence_is_reported(self) -> None:
        text = "De commissie stelde na een uitvoerige vergadering waarin alle afdelingen hun afzonderlijke bevindingen toelichtten en verschillende bestuurders aanvullende vragen formuleerden uiteindelijk vast dat de geplande uitvoering van het omvangrijke besluitvormingsproces opnieuw moest worden uitgesteld tot een nader te bepalen datum."
        report = self.language_report(text)
        self.assertFalse(report["pass"])
        self.assertEqual(report["long_sentence_count"], 1)
        self.assertIn("excessively_long_sentences", report["spoken_language_issues"])

    def test_factual_restrained_dutch_passes(self) -> None:
        text = "De rechtbank hoort op 12 maart 2012 drie getuigen. Twee van hen bevestigen dat de winkel om acht uur sloot.\n\nDe camerabeelden tonen vervolgens één auto bij de achteringang. Het kenteken is niet leesbaar."
        report = self.language_report(text)
        self.assertTrue(report["pass"])

    def test_english_script_skips_dutch_only_rules(self) -> None:
        report = self.language_report("What happened next would change everything. What happened next would change everything.", "English")
        self.assertEqual(report["dutch_language_quality"], "not_applicable")
        self.assertEqual(report["translated_english_patterns"], [])
        self.assertEqual(report["language_rejection_reasons"], [])

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
        for field in ("word_count", "estimated_duration_minutes", "missing_beat_ids", "unsupported_claim_ids", "unused_required_research_details", "banned_style_phrases", "repetitive_transitions", "opening_quality", "ending_quality", "dutch_language_quality", "translated_english_patterns", "unnatural_phrasing", "repeated_sentence_patterns", "overdramatic_phrases", "spoken_language_issues", "long_sentence_count", "connector_repetition", "language_rejection_reasons", "failure_reasons"):
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

    def test_bounded_retry_validates_each_candidate_and_stops_on_first_pass(self) -> None:
        class Provider:
            available = True
            calls = 0

            def rewrite_script(self, *args, repair_plan=None, **kwargs):
                self.calls += 1
                self.repair_plan = args[4] if len(args) > 4 else repair_plan
                return {"candidate": self.calls + 1}

        reports = [
            {"pass": False, "failure_reasons": ["too short"]},
            {"pass": True, "failure_reasons": []},
        ]
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "manifests").mkdir()
            with patch("inside_case_factory.core.script_repair.validate_script", side_effect=reports) as validator:
                accepted, attempts = _generate_validated_script_candidates(
                    root, {"candidate": 1}, provider, [], {}, {"maximum_revision_attempts": 99},
                    {}, {}, {}, 3, "Nederlands",
                )
            self.assertEqual(accepted, {"candidate": 2})
            self.assertEqual(len(attempts), 2)
            self.assertEqual(validator.call_count, 2)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(provider.repair_plan["source_failure_reasons"], ["too short"])
            self.assertEqual(read_json(root / "manifests/script.json")["accepted_candidate_id"], 2)

    def test_bounded_retry_never_exceeds_three_attempts(self) -> None:
        class Provider:
            available = True
            calls = 0

            def rewrite_script(self, *args, **kwargs):
                self.calls += 1
                return {"candidate": self.calls + 1}

        rejection = {"pass": False, "failure_reasons": ["validator rejection"]}
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "manifests").mkdir()
            with patch("inside_case_factory.core.script_repair.validate_script", return_value=rejection) as validator:
                accepted, attempts = _generate_validated_script_candidates(
                    root, {"candidate": 1}, provider, [], {}, {"maximum_revision_attempts": 50},
                    {}, {}, {}, 3, "Nederlands",
                )
            self.assertIsNone(accepted)
            self.assertEqual(len(attempts), 3)
            self.assertEqual(validator.call_count, 3)
            self.assertEqual(provider.calls, 2)
            self.assertFalse((root / "manifests/script.json").exists())
            self.assertFalse((root / "manifests/accepted_script_artifact.json").exists())

    def test_bounded_retry_requires_real_validator_reasons(self) -> None:
        class Provider:
            available = True
            calls = 0

            def rewrite_script(self, *args, **kwargs):
                self.calls += 1
                return {}

        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "manifests").mkdir()
            with patch("inside_case_factory.core.script_repair.validate_script", return_value={"pass": False, "failure_reasons": []}):
                accepted, attempts = _generate_validated_script_candidates(
                    root, {}, provider, [], {}, {"maximum_revision_attempts": 2}, {}, {}, {}, 3, "Nederlands",
                )
            self.assertIsNone(accepted)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(provider.calls, 0)

    def test_rewriter_preserves_paid_confirmation_and_budget_gates(self) -> None:
        config = ReasoningConfig(enabled=True, dry_run=False, require_explicit_confirmation=True,
                                 per_project_spending_limit_usd=0.25)
        provider = OpenAIReasoningProvider(config, api_key="test-key")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "manifests").mkdir()
            with self.assertRaisesRegex(ReasoningProviderError, "Paid API call not confirmed"):
                provider.rewrite_script(root, {}, [], {}, {"issues": []}, {}, {}, {}, 3, "Nederlands")
            write_json(root / "manifests/paid_api_confirmation.json", {"confirmed": True})
            write_json(root / "manifests/reasoning_usage.json", {"token_based_estimated_total_cost_usd": 0.30})
            provider.config = ReasoningConfig(enabled=True, dry_run=False, require_explicit_confirmation=True,
                                              per_project_spending_limit_usd=0.25)
            with self.assertRaisesRegex(ReasoningProviderError, "budget would be exceeded"):
                provider.rewrite_script(root, {}, [], {}, {"issues": []}, {}, {}, {}, 3, "Nederlands")


if __name__ == "__main__":
    unittest.main()
