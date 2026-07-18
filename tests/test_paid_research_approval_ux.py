from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.config.settings import load_settings
from inside_case_factory.core.production import run_research
from inside_case_factory.core.project import create_project
from inside_case_factory.core.user_experience import production_progress
from inside_case_factory.providers.reasoning import paid_api_confirmed
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


ROOT = Path(__file__).resolve().parents[1]


class PaidResearchApprovalUXTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Approval Case").root
        write_json(self.root / "manifests/cost_estimate.json", {
            "project_budget_usd": .25, "stages": [
                {"stage": "research_plan", "estimated_maximum_cost_usd": .01},
                {"stage": "source_analysis", "estimated_maximum_cost_usd": .02},
                {"stage": "script", "estimated_maximum_cost_usd": .1},
            ],
        })
        write_json(self.root / "manifests/orchestration.json", {"status": "blocked", "current_stage": "research_plan", "last_error": "Paid API call not confirmed."})
        write_json(self.root / "manifests/production_plan.json", {"topic": "Approval Case", "stages": []})
        write_json(self.root / "manifests/production_request.json", {"prompt": "Approval Case"})
        self.app = DashboardApp(ROOT)
        self.app.project_root = lambda slug: self.root  # type: ignore[method-assign]

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_confirmation_is_approval_required_without_fake_progress(self):
        result = production_progress(self.root)
        research = next(item for item in result["phases"] if item["name"] == "Onderzoek")
        self.assertEqual(research["status"], "approval_required")
        self.assertEqual(research["progress"], 0)
        self.assertNotEqual(result["percentage"], 30)
        self.assertEqual(result["estimated_remaining"], "")
        self.assertTrue(result["paid_gate"]["required"])

    def test_action_card_shows_provider_limit_purpose_and_safe_actions(self):
        html = self.app.production_overview_page("approval-case")
        for text in ("Onderzoek wacht op jouw toestemming", "maximum_cost_usd", "provider", "purpose", "Kosten goedkeuren en doorgaan", "Annuleren"):
            self.assertIn(text, html)
        self.assertIn("approval-card", html)

    def test_approval_is_project_specific_audited_and_resumes_orchestrator(self):
        with patch.object(self.app, "resume_managed_production") as resume:
            response = self.app.paid_research_action("approval-case", "approve")
        self.assertEqual(response[0], "303 See Other")
        confirmation = read_json(self.root / "manifests/paid_api_confirmation.json")
        self.assertTrue(confirmation["confirmed"])
        self.assertEqual(confirmation["project"], self.root.name)
        self.assertEqual(confirmation["approved_limit_usd"], .03)
        self.assertEqual(confirmation["operations"], ["research_plan", "source_analysis", "tavily_research"])
        self.assertTrue(paid_api_confirmed(self.root, "research_plan", .01))
        self.assertFalse(paid_api_confirmed(self.root, "script", .01))
        resume.assert_called_once_with(self.root)
        event = read_json(self.root / "manifests/progress_events.json")["events"][-1]
        self.assertEqual(event["approved_limit_usd"], .03)
        self.assertIn("at", event)

    def test_local_fallback_requires_existing_sources_and_claims(self):
        unavailable = self.app.paid_research_action("approval-case", "fallback")
        self.assertEqual(unavailable[0], "409 Conflict")
        write_json(self.root / "manifests/sources.json", {"sources": [{"id": "s1", "review_status": "pending_review"}]})
        write_json(self.root / "manifests/claims.json", {"claims": [{"id": "c1", "source_ids": ["s1"], "review_status": "pending_review"}]})
        with patch.object(self.app, "resume_managed_production") as resume:
            response = self.app.paid_research_action("approval-case", "fallback")
        self.assertEqual(response[0], "303 See Other")
        self.assertEqual(read_json(self.root / "manifests/research_plan.json")["provider"], "local_fallback")
        self.assertFalse(read_json(self.root / "manifests/paid_api_confirmation.json")["confirmed"])
        resume.assert_called_once()

    def test_missing_api_key_becomes_clear_blocked_error(self):
        confirmation = {"confirmed": True, "project": self.root.name, "approved_limit_usd": .03, "operations": ["research_plan", "source_analysis", "tavily_research"]}
        write_json(self.root / "manifests/paid_api_confirmation.json", confirmation)
        settings = load_settings(ROOT)
        with patch.dict(os.environ, {}, clear=True):
            result = run_research(settings, self.root, "Approval Case")
        self.assertFalse(result["ok"])
        self.assertIn("TAVILY_API_KEY is not set", result["message"])

    def test_budget_limit_blocks_confirmation(self):
        estimate = read_json(self.root / "manifests/cost_estimate.json"); estimate["project_budget_usd"] = .01
        write_json(self.root / "manifests/cost_estimate.json", estimate)
        response = self.app.paid_research_action("approval-case", "approve")
        self.assertEqual(response[0], "409 Conflict")
        self.assertFalse((self.root / "manifests/paid_api_confirmation.json").exists())

    def test_approval_card_remains_mobile_friendly(self):
        html = self.app.production_overview_page("approval-case")
        self.assertIn("max-width: 620px", html)
        self.assertIn(".approval-card", html)
        self.assertIn("grid-template-columns:1fr", html)


if __name__ == "__main__":
    unittest.main()
