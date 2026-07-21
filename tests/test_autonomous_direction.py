from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.autonomous_direction import (
    CRITIC_CATEGORIES,
    CriticEngine,
    DirectorEngine,
    QualityPolicy,
    improvement_decision,
    record_feedback,
    save_improvement_state,
)
from inside_case_factory.core.project import create_project
from inside_case_factory.core.relevance import (
    rebuild_relevance_cache,
    score_scene_asset_match,
    score_source_policy,
    validate_scene_asset_gate,
)
from inside_case_factory.utils.files import read_json, write_json


def scenes() -> list[dict[str, object]]:
    return [
        {"id": f"s{i:02}", "heading": f"Chapter {i}", "duration_seconds": 12.0,
         "start_seconds": (i - 1) * 12.0, "claim_ids": [f"c{i}"], "dates": ["2001"],
         "events": ["A documented event"]}
        for i in range(1, 5)
    ]


class AutonomousDirectionTests(unittest.TestCase):
    def project(self, root: Path) -> Path:
        project = create_project(root, "Director Case").root
        write_json(project / "manifests" / "media_sources.json", {"assets": []})
        workflow = read_json(project / "manifests" / "workflow.json")
        workflow["run_quality_mode"] = "sample_or_demo"
        write_json(project / "manifests" / "workflow.json", workflow)
        return project

    def diversity_scene(self) -> list[dict[str, object]]:
        return [{
            "id": "s01",
            "heading": "Police report",
            "duration_seconds": 18.0,
            "claim_ids": ["c1"],
            "people": ["Michael Jackson"],
            "locations": ["Los Angeles"],
            "events": ["death investigation"],
            "dates": ["2009"],
            "media_requirements": "A police report for the death investigation.",
            "archival_media_queries": ["michael jackson death police report"],
            "alternative_media_queries": ["michael jackson investigation"],
        }]

    def test_director_plans_complete_story_arc_and_effect_restraint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            plan = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            roles = {item["story_role"] for item in plan["scenes"]}
            self.assertEqual(roles, {"hook", "development", "climax", "outro"})
            self.assertTrue(any(not shot["apply_effect"] for scene in plan["scenes"] for shot in scene["shots"]))
            self.assertTrue((root / "manifests" / "director_report.json").exists())

    def test_critic_scores_every_required_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            (root / "manifests" / "subtitles.srt").write_text("subtitle", encoding="utf-8")
            report = CriticEngine().analyze(root, render_number=1, duration_seconds=48.0)
            self.assertEqual(set(report["scores"]), set(CRITIC_CATEGORIES))
            self.assertTrue(all(0 <= score <= 100 for score in report["scores"].values()))

    def test_low_score_requests_rerender_within_limit(self) -> None:
        policy = QualityPolicy(threshold=85, max_renders=2)
        decision = improvement_decision({"overall_score": 70}, policy, 1)
        self.assertTrue(decision["rerender_required"])

    def test_render_count_and_cost_budgets_stop_loop(self) -> None:
        self.assertFalse(improvement_decision({"overall_score": 70}, QualityPolicy(threshold=85, max_renders=1), 1)["rerender_required"])
        costly = QualityPolicy(threshold=85, max_renders=3, max_rerender_cost_usd=0.1, estimated_rerender_cost_usd=0.2)
        self.assertFalse(improvement_decision({"overall_score": 70}, costly, 1)["rerender_required"])

    def test_feedback_requires_explicit_approval_before_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            record_feedback(root, ["Reduce repetitive transitions."])
            first = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            self.assertEqual(first["director"]["approved_lessons_applied"], [])
            feedback = read_json(root / "manifests" / "critic_feedback.json")
            feedback["entries"][0]["approval_status"] = "approved"
            write_json(root / "manifests" / "critic_feedback.json", feedback)
            second = DirectorEngine().plan(root, scenes(), width=1920, height=1080)
            self.assertEqual(second["director"]["approved_lessons_applied"], ["feedback-1"])

    def test_quality_state_recovers_after_interrupted_rerender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            report = {"overall_score": 60, "weak_categories": ["pacing"]}
            decision = improvement_decision(report, QualityPolicy(threshold=80, max_renders=2), 1)
            save_improvement_state(root, decision, report)
            recovered = read_json(root / "manifests" / "quality_cycle.json")
            self.assertEqual(recovered["status"], "rerender_pending")
            self.assertEqual(len(recovered["attempts"]), 1)

    def test_state_and_feedback_writes_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            report = {"overall_score": 60, "weak_categories": ["pacing"]}
            decision = improvement_decision(report, QualityPolicy(threshold=80, max_renders=2), 1)
            save_improvement_state(root, decision, report)
            save_improvement_state(root, decision, report)
            record_feedback(root, ["Improve pacing."])
            record_feedback(root, ["Improve pacing."])
            self.assertEqual(len(read_json(root / "manifests" / "quality_cycle.json")["attempts"]), 1)
            self.assertEqual(len(read_json(root / "manifests" / "critic_feedback.json")["entries"]), 1)

    def test_scene_asset_gate_accepts_matching_eligible_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-pass",
                "title": "Police report about Michael Jackson death",
                "description": "Document from 2009 about the Michael Jackson death investigation",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/report",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "mapped_scenes": ["s01"],
                "suggested_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "project_slug": root.name,
            }
            preview_path = root / asset["path"]
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text("preview", encoding="utf-8")
            scene = {
                "id": "s01",
                "heading": "Police report",
                "duration_seconds": 12.0,
                "claim_ids": ["c1"],
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
                "archival_media_queries": ["michael jackson death police report"],
                "alternative_media_queries": ["michael jackson investigation"],
                "shots": [{"id": "s01-shot-1", "asset": asset, "media_intent": {"subject": "Police report", "people": ["Michael Jackson"], "locations": ["Los Angeles"], "event": ["death investigation"], "search_terms": ["michael jackson death police report"], "aliases": [], "content_reason": "A police report for the death investigation."}}],
            }
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertTrue(result["passed"])
            self.assertEqual(result["results"][0]["accepted_asset_ids"], ["asset-pass"])

    def test_scene_asset_gate_rejects_generic_topical_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-generic",
                "title": "Michael Jackson overview",
                "description": "A generic overview of Michael Jackson",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/generic",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "project_slug": root.name,
            }
            scene = {
                "id": "s01",
                "heading": "Police report",
                "duration_seconds": 12.0,
                "claim_ids": ["c1"],
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
                "archival_media_queries": ["michael jackson death police report"],
                "alternative_media_queries": ["michael jackson investigation"],
                "shots": [{"id": "s01-shot-1", "asset": asset, "media_intent": {"subject": "Police report", "people": ["Michael Jackson"], "locations": ["Los Angeles"], "event": ["death investigation"], "search_terms": ["michael jackson death police report"], "aliases": [], "content_reason": "A police report for the death investigation."}}],
            }
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertFalse(result["passed"])
            self.assertEqual(result["results"][0]["rejected_asset_ids"], ["asset-generic"])
            self.assertIn("scene-intent", " ".join(result["results"][0]["rejection_reasons"]))

    def test_scene_asset_gate_rejects_ineligible_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-ineligible",
                "title": "Unrelated travel photo",
                "description": "A random travel photo",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/travel",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": False,
                "relevance_score": 0.1,
                "mapped_scenes": ["s01"],
                "suggested_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "project_slug": root.name,
            }
            scene = {
                "id": "s01",
                "heading": "Police report",
                "duration_seconds": 12.0,
                "claim_ids": ["c1"],
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
                "archival_media_queries": ["michael jackson death police report"],
                "alternative_media_queries": ["michael jackson investigation"],
                "shots": [{"id": "s01-shot-1", "asset": asset, "media_intent": {"subject": "Police report", "people": ["Michael Jackson"], "locations": ["Los Angeles"], "event": ["death investigation"], "search_terms": ["michael jackson death police report"], "aliases": [], "content_reason": "A police report for the death investigation."}}],
            }
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertFalse(result["passed"])
            self.assertEqual(result["results"][0]["rejected_asset_ids"], ["asset-ineligible"])

    def test_scene_asset_gate_blocks_director_finalization_for_unacceptable_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            write_json(root / "manifests" / "media_sources.json", {
                "assets": [{
                    "id": "asset-generic",
                    "title": "Michael Jackson overview",
                    "description": "A generic overview of Michael Jackson",
                    "path": "assets/images/preview.jpg",
                    "source_url": "https://example.com/generic",
                    "rights_status": "approved",
                    "review_status": "approved",
                    "review_eligible": True,
                    "relevance_score": 0.9,
                    "project_slug": root.name,
                }]
            })
            scenes = [{"id": "s01", "heading": "Police report", "duration_seconds": 12.0, "claim_ids": ["c1"], "people": ["Michael Jackson"], "locations": ["Los Angeles"], "events": ["death investigation"], "dates": ["2009"], "media_requirements": "A police report for the death investigation.", "archival_media_queries": ["michael jackson death police report"], "alternative_media_queries": ["michael jackson investigation"]}]
            with self.assertRaisesRegex(RuntimeError, "Asset gate blocked"):
                DirectorEngine().plan(root, scenes, width=1920, height=1080)
            self.assertTrue((root / "manifests" / "director_report.json").exists())

    def test_scene_asset_gate_evaluates_multiple_scenes_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            good = {
                "id": "asset-good",
                "title": "Police report about Michael Jackson death",
                "description": "Document from 2009 about the Michael Jackson death investigation",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/report",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "mapped_scenes": ["s01"],
                "suggested_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "project_slug": root.name,
            }
            bad = {
                "id": "asset-bad",
                "title": "Michael Jackson overview",
                "description": "A generic overview of Michael Jackson",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/generic",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "project_slug": root.name,
            }
            scenes = [
                {"id": "s01", "heading": "Police report", "duration_seconds": 12.0, "claim_ids": ["c1"], "people": ["Michael Jackson"], "locations": ["Los Angeles"], "events": ["death investigation"], "dates": ["2009"], "media_requirements": "A police report for the death investigation.", "archival_media_queries": ["michael jackson death police report"], "alternative_media_queries": ["michael jackson investigation"]},
                {"id": "s02", "heading": "Family response", "duration_seconds": 12.0, "claim_ids": ["c2"], "people": ["Janet Jackson"], "locations": ["Los Angeles"], "events": ["family response"], "dates": ["2009"], "media_requirements": "Family response footage for the immediate aftermath.", "archival_media_queries": ["janet jackson family response"], "alternative_media_queries": ["family response"], "shots": []},
            ]
            write_json(root / "manifests" / "media_sources.json", {"assets": [good, bad]})
            result = validate_scene_asset_gate(root, scenes, media_assets=[good, bad])
            self.assertEqual(len(result["results"]), 2)
            self.assertTrue(result["results"][0]["passed"])
            self.assertFalse(result["results"][1]["passed"])
            self.assertEqual(result["results"][0]["accepted_asset_ids"], ["asset-good"])
            self.assertEqual(result["results"][1]["rejected_asset_ids"], ["asset-good", "asset-bad"])

    def test_valid_director_planning_still_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-pass",
                "title": "Police report about Michael Jackson death",
                "description": "Document from 2009 about the Michael Jackson death investigation",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/report",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "mapped_scenes": ["s01"],
                "suggested_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "project_slug": root.name,
            }
            preview_path = root / asset["path"]
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text("preview", encoding="utf-8")
            write_json(root / "manifests" / "media_sources.json", {"assets": [asset]})
            scenes = [{"id": "s01", "heading": "Police report", "duration_seconds": 12.0, "claim_ids": ["c1"], "people": ["Michael Jackson"], "locations": ["Los Angeles"], "events": ["death investigation"], "dates": ["2009"], "media_requirements": "A police report for the death investigation.", "archival_media_queries": ["michael jackson death police report"], "alternative_media_queries": ["michael jackson investigation"]}]
            plan = DirectorEngine().plan(root, scenes, width=1920, height=1080)
            self.assertTrue(plan["director"]["status"] == "ready")
            self.assertTrue((root / "manifests" / "shot_media_manifest.json").exists())

    def test_relevant_archival_footage_ranks_above_equally_relevant_generic_stock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            write_json(root / "manifests" / "project.json", {"topic": "Michael Jackson death investigation"})
            write_json(root / "manifests" / "research_plan.json", {"people": ["Michael Jackson"], "events": ["death investigation"], "dates": ["2009"], "requested_focus": "Police report", "factual_questions": ["What happened?"]})
            write_json(root / "manifests" / "production_request.json", {"prompt": "Create a documentary about the death investigation."})
            write_json(root / "manifests" / "scenes.json", {"scenes": [{"id": "s01", "heading": "Police report", "people": ["Michael Jackson"], "locations": ["Los Angeles"], "events": ["death investigation"], "dates": ["2009"], "media_requirements": "A police report for the death investigation."}]})
            archival = {"id": "asset-archival", "title": "Archival police report footage", "description": "Archival footage from the 2009 police report investigation", "path": "assets/archival.mp4", "source_url": "https://example.com/archival", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.8, "project_slug": root.name}
            generic = {"id": "asset-generic", "title": "Generic city skyline B-roll", "description": "Generic stock footage of city skylines and buildings", "path": "assets/generic.mp4", "source_url": "https://example.com/generic", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.8, "project_slug": root.name}
            write_json(root / "manifests" / "media_sources.json", {"assets": [archival, generic]})
            rebuild_relevance_cache(root)
            media = read_json(root / "manifests" / "media_sources.json")
            archival_record = next(item for item in media["assets"] if item["id"] == "asset-archival")
            generic_record = next(item for item in media["assets"] if item["id"] == "asset-generic")
            self.assertEqual(archival_record["source_category"], "archival_footage")
            self.assertGreater(archival_record["source_policy_score"], generic_record["source_policy_score"])
            self.assertGreater(archival_record["relevance_score"], generic_record["relevance_score"])

    def test_relevant_historical_photo_ranks_above_generic_city_broll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            historical = {"id": "asset-history", "title": "Historical photograph of the courtroom", "description": "Historical photo from a museum archive", "source_url": "https://example.com/history", "rights_status": "public_domain", "review_status": "approved", "review_eligible": True, "relevance_score": 0.75, "project_slug": root.name}
            generic = {"id": "asset-generic", "title": "Generic city skyline B-roll", "description": "Generic stock footage of buildings and streets", "source_url": "https://example.com/generic", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.75, "project_slug": root.name}
            policy_history = score_source_policy(historical)
            policy_generic = score_source_policy(generic)
            self.assertEqual(policy_history["source_category"], "historical_photographs")
            self.assertGreater(policy_history["source_policy_score"], policy_generic["source_policy_score"])
            self.assertGreater(policy_history["rights_confidence"], policy_generic["rights_confidence"])

    def test_semantically_irrelevant_archival_footage_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {"id": "asset-archival-irrelevant", "title": "Archival footage of an old parade", "description": "Historic footage from a city parade", "source_url": "https://example.com/archive", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "project_slug": root.name}
            scene = {"id": "s01", "heading": "Police report", "people": ["Michael Jackson"], "locations": ["Los Angeles"], "events": ["death investigation"], "dates": ["2009"], "media_requirements": "A police report for the death investigation."}
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertFalse(result["passed"])
            self.assertIn("scene-match", " ".join(result["results"][0]["rejection_reasons"]))

    def test_generic_stock_remains_usable_when_it_is_the_only_valid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {"id": "asset-generic", "title": "Generic city skyline B-roll", "description": "Generic stock footage of buildings and streets", "source_url": "https://example.com/generic", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "project_slug": root.name}
            scene = {"id": "s01", "heading": "Police report"}
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertTrue(result["passed"])
            self.assertEqual(result["results"][0]["accepted_asset_ids"], ["asset-generic"])

    def test_empty_media_set_blocks_asset_gate_in_evidence_grade_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            scene = {"id": "s01", "heading": "Police report"}
            result = validate_scene_asset_gate(root, [scene], media_assets=[], run_quality_mode="evidence_grade")
            self.assertFalse(result["passed"])
            self.assertEqual(result["run_quality_mode"], "evidence_grade")
            self.assertIn("s01", result["blocked_scene_ids"])
            self.assertIn("no-candidates", result["results"][0]["rejection_reasons"])

    def test_empty_media_set_uses_demo_fallback_in_sample_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            scene = {"id": "s01", "heading": "Police report"}
            result = validate_scene_asset_gate(root, [scene], media_assets=[], run_quality_mode="sample_or_demo")
            self.assertTrue(result["passed"])
            self.assertEqual(result["run_quality_mode"], "sample_or_demo")
            self.assertTrue(result["fallback_mode_used"])

    def test_ai_generated_media_is_deprioritized_when_real_media_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            real_asset = {"id": "asset-real", "title": "Archival police report footage", "description": "Historic footage from a police report archive", "source_url": "https://example.com/real", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "project_slug": root.name}
            synthetic_asset = {"id": "asset-ai", "title": "AI-generated police report scene", "description": "Synthetic image generated by an AI model", "source_url": "https://example.com/ai", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "project_slug": root.name}
            real_policy = score_source_policy(real_asset)
            synthetic_policy = score_source_policy(synthetic_asset)
            self.assertGreater(real_policy["source_policy_score"], synthetic_policy["source_policy_score"])
            self.assertGreater(real_policy["rights_confidence"], synthetic_policy["rights_confidence"])

    def test_rights_safe_institutional_material_receives_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            institutional = {"id": "asset-institutional", "title": "National archives photograph", "description": "Public-domain government archive image", "source_url": "https://example.com/archive", "rights_status": "public_domain", "review_status": "approved", "review_eligible": True, "relevance_score": 0.85, "project_slug": root.name}
            generic = {"id": "asset-generic", "title": "Generic city skyline B-roll", "description": "Generic stock footage of buildings and streets", "source_url": "https://example.com/generic", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.85, "project_slug": root.name}
            institutional_policy = score_source_policy(institutional)
            generic_policy = score_source_policy(generic)
            self.assertGreater(institutional_policy["source_policy_score"], generic_policy["source_policy_score"])
            self.assertGreater(institutional_policy["rights_confidence"], generic_policy["rights_confidence"])
            self.assertEqual(institutional_policy["source_category"], "government_institutional_archive")

    def test_scene_semantic_match_ranks_specific_scene_asset_above_generic_topical_footage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            scene = {
                "id": "s01",
                "heading": "Police report",
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
                "archival_media_queries": ["michael jackson death police report"],
                "alternative_media_queries": ["michael jackson investigation"],
            }
            specific = {"id": "asset-specific", "title": "Police report about Michael Jackson death investigation", "description": "A 2009 police report from Los Angeles documenting the death investigation."}
            generic = {"id": "asset-generic", "title": "Michael Jackson overview", "description": "A generic overview of Michael Jackson with city skyline footage."}
            specific_result = score_scene_asset_match(root, scene, specific)
            generic_result = score_scene_asset_match(root, scene, generic)
            self.assertGreater(specific_result["semantic_match_score"], generic_result["semantic_match_score"])
            self.assertTrue(specific_result["final_scene_match_passed"])
            self.assertFalse(generic_result["final_scene_match_passed"])
            self.assertGreater(generic_result["generic_visual_penalty"], 0.0)

    def test_scene_semantic_match_fails_when_required_location_action_or_object_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            scene = {
                "id": "s01",
                "heading": "Police report",
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
            }
            asset = {"id": "asset-mismatch", "title": "Office interior", "description": "An office meeting room with executives discussing business."}
            result = score_scene_asset_match(root, scene, asset)
            self.assertFalse(result["final_scene_match_passed"])
            self.assertTrue(result["missing_required_concepts"])
            self.assertIn("scene-mismatch", result["mismatch_reasons"])

    def test_scene_semantic_match_fails_for_contradictory_time_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            scene = {
                "id": "s01",
                "heading": "Police report",
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
            }
            asset = {"id": "asset-contradiction", "title": "1970s newsroom footage", "description": "Footage from the 1970s showing a newsroom."}
            result = score_scene_asset_match(root, scene, asset)
            self.assertFalse(result["final_scene_match_passed"])
            self.assertTrue(any("time_period" in reason for reason in result["mismatch_reasons"]))

    def test_asset_gate_uses_scene_match_result_for_generic_footage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-generic",
                "title": "Michael Jackson overview",
                "description": "A generic overview of Michael Jackson with city skyline footage",
                "path": "assets/images/preview.jpg",
                "source_url": "https://example.com/generic",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.9,
                "mapped_scenes": ["s01"],
                "project_slug": root.name,
            }
            preview_path = root / asset["path"]
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text("preview", encoding="utf-8")
            scene = {
                "id": "s01",
                "heading": "Police report",
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["death investigation"],
                "dates": ["2009"],
                "media_requirements": "A police report for the death investigation.",
                "archival_media_queries": ["michael jackson death police report"],
            }
            result = validate_scene_asset_gate(root, [scene], media_assets=[asset])
            self.assertFalse(result["passed"])
            self.assertIn("scene-match", " ".join(result["results"][0]["rejection_reasons"]))

    def test_repeated_asset_rejection_adds_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            asset = {
                "id": "asset-single",
                "title": "Police report about Michael Jackson death",
                "description": "Document from 2009 about the Michael Jackson death investigation",
                "path": "assets/report.jpg",
                "source_url": "https://example.com/archive/report",
                "rights_status": "approved",
                "review_status": "approved",
                "review_eligible": True,
                "relevance_score": 0.95,
                "semantic_match_score": 0.9,
                "scene_match_passed": True,
                "source_policy_score": 0.9,
                "archival_priority": True,
                "mapped_scenes": ["s01"],
                "suggested_scenes": ["s01"],
                "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"],
                "project_slug": root.name,
            }
            write_json(root / "manifests" / "media_sources.json", {"assets": [asset]})
            plan = DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            self.assertGreater(plan["director"]["visual_variety"]["repetition_penalty"], 0.0)

    def test_repeated_source_penalty_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            assets = [
                {"id": "asset-a", "title": "Police report evidence A", "description": "Michael Jackson death investigation in Los Angeles 2009", "source_url": "https://archive.example.com/a", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.85, "archival_priority": True, "mapped_scenes": ["s01"], "suggested_scenes": ["s01"], "shot_ids": ["s01-shot-1"], "project_slug": root.name},
                {"id": "asset-b", "title": "Police report evidence B", "description": "Michael Jackson death investigation in Los Angeles 2009", "source_url": "https://archive.example.com/b", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.85, "archival_priority": True, "mapped_scenes": ["s01"], "suggested_scenes": ["s01"], "shot_ids": ["s01-shot-2"], "project_slug": root.name},
                {"id": "asset-c", "title": "Police report evidence C", "description": "Michael Jackson death investigation in Los Angeles 2009", "source_url": "https://archive.example.com/c", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.85, "archival_priority": True, "mapped_scenes": ["s01"], "suggested_scenes": ["s01"], "shot_ids": ["s01-shot-3"], "project_slug": root.name},
            ]
            write_json(root / "manifests" / "media_sources.json", {"assets": assets})
            plan = DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            self.assertGreater(plan["director"]["visual_variety"]["repetition_penalty"], 0.0)
            self.assertLess(plan["director"]["visual_variety"]["source_diversity_score"], 1.0)

    def test_excessive_still_images_increase_static_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            assets = [
                {"id": "still-1", "title": "Historical photograph one", "description": "Michael Jackson death investigation Los Angeles 2009", "type": "image", "path": "assets/one.jpg", "source_url": "https://example.com/one", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.92, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.86, "archival_priority": True, "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-1"], "project_slug": root.name},
                {"id": "still-2", "title": "Historical photograph two", "description": "Michael Jackson death investigation Los Angeles 2009", "type": "image", "path": "assets/two.jpg", "source_url": "https://example.com/two", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.91, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.86, "archival_priority": True, "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-2"], "project_slug": root.name},
                {"id": "still-3", "title": "Historical photograph three", "description": "Michael Jackson death investigation Los Angeles 2009", "type": "image", "path": "assets/three.jpg", "source_url": "https://example.com/three", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.86, "archival_priority": True, "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-3"], "project_slug": root.name},
            ]
            write_json(root / "manifests" / "media_sources.json", {"assets": assets})
            plan = DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            self.assertGreater(plan["director"]["visual_variety"]["static_penalty"], 0.0)

    def test_diversity_pass_improves_alternating_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            primary = {"id": "asset-primary", "title": "Police report footage", "description": "Michael Jackson death investigation Los Angeles 2009", "source_url": "https://source-a.example.com/primary", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.94, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.9, "archival_priority": True, "mapped_scenes": ["s01"], "suggested_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            alternate = {"id": "asset-alternate", "title": "Police report archive angle", "description": "Michael Jackson death investigation Los Angeles 2009 archival perspective", "source_url": "https://source-b.example.com/alternate", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.92, "semantic_match_score": 0.89, "scene_match_passed": True, "source_policy_score": 0.89, "archival_priority": True, "mapped_scenes": ["s01"], "suggested_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            write_json(root / "manifests" / "media_sources.json", {"assets": [primary, alternate]})
            DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            shot_manifest = read_json(root / "manifests" / "shot_media_manifest.json")
            chosen = [str(row.get("chosen_asset", {}).get("id", "")) for row in shot_manifest.get("shots", [])]
            self.assertGreaterEqual(len(set(chosen)), 2)

    def test_diversity_does_not_replace_strong_semantic_asset_with_irrelevant_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            strong = {"id": "asset-strong", "title": "Police report evidence", "description": "Michael Jackson death investigation Los Angeles 2009 official report", "source_url": "https://archive.example.com/strong", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.93, "semantic_match_score": 0.92, "scene_match_passed": True, "source_policy_score": 0.9, "archival_priority": True, "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            weak = {"id": "asset-weak", "title": "City skyline", "description": "Generic overview footage", "source_url": "https://other.example.com/weak", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.9, "semantic_match_score": 0.12, "scene_match_passed": False, "source_policy_score": 0.2, "archival_priority": False, "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            write_json(root / "manifests" / "media_sources.json", {"assets": [strong, weak]})
            DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            shot_manifest = read_json(root / "manifests" / "shot_media_manifest.json")
            chosen = [str(row.get("chosen_asset", {}).get("id", "")) for row in shot_manifest.get("shots", [])]
            self.assertNotIn("asset-weak", chosen)

    def test_diversity_preserves_archival_first_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(Path(tmp))
            archival = {"id": "asset-archival", "title": "Archival police report footage", "description": "Michael Jackson death investigation Los Angeles 2009", "source_url": "https://archive.example.com/archival", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.93, "semantic_match_score": 0.9, "scene_match_passed": True, "source_policy_score": 0.92, "archival_priority": True, "source_category": "archival_footage", "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            generic = {"id": "asset-generic", "title": "Generic B-roll alternative", "description": "Michael Jackson overview city footage", "source_url": "https://stock.example.com/generic", "rights_status": "approved", "review_status": "approved", "review_eligible": True, "relevance_score": 0.92, "semantic_match_score": 0.88, "scene_match_passed": True, "source_policy_score": 0.2, "archival_priority": False, "source_category": "generic_stock_footage", "mapped_scenes": ["s01"], "shot_ids": ["s01-shot-1", "s01-shot-2", "s01-shot-3"], "project_slug": root.name}
            write_json(root / "manifests" / "media_sources.json", {"assets": [archival, generic]})
            DirectorEngine().plan(root, self.diversity_scene(), width=1920, height=1080)
            shot_manifest = read_json(root / "manifests" / "shot_media_manifest.json")
            chosen = [row.get("chosen_asset", {}) for row in shot_manifest.get("shots", [])]
            self.assertTrue(all(bool(item.get("archival_priority", False)) for item in chosen))


if __name__ == "__main__":
    unittest.main()
