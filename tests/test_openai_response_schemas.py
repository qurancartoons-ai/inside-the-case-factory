from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.project import create_project
from inside_case_factory.core.production import recover_invalid_schema_task
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp
from inside_case_factory.providers.reasoning import OpenAIReasoningProvider, RESEARCH_PLAN_SCHEMA, RESPONSE_FORMAT_SCHEMAS, ReasoningConfig, ReasoningProviderError, validate_strict_response_schema


class _Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return json.dumps(self.payload).encode()


class OpenAIResponseSchemaTests(unittest.TestCase):
    def test_every_response_format_is_strict_recursively(self):
        for schema in RESPONSE_FORMAT_SCHEMAS:
            self.assertEqual(validate_strict_response_schema(schema), [], schema["name"])

    def test_required_exactly_matches_properties_at_every_object(self):
        def visit(node):
            if not isinstance(node, dict): return
            if node.get("type") == "object":
                self.assertEqual(set(node["required"]), set(node["properties"]))
                self.assertIs(node["additionalProperties"], False)
                for child in node["properties"].values(): visit(child)
            if node.get("type") == "array": visit(node["items"])
        for schema in RESPONSE_FORMAT_SCHEMAS: visit(schema["schema"])

    def test_nested_schema_error_is_detected_before_provider_call(self):
        broken = deepcopy(RESEARCH_PLAN_SCHEMA)
        del broken["schema"]["properties"]["involved_countries"]["items"]["required"]
        self.assertTrue(any("involved_countries.items" in error for error in validate_strict_response_schema(broken)))
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), "Schema guard")
            provider = OpenAIReasoningProvider(ReasoningConfig(enabled=True), api_key="test")
            with patch("inside_case_factory.providers.reasoning.urlopen") as call:
                with self.assertRaisesRegex(ReasoningProviderError, "Invalid local response schema"):
                    provider._json_response(project.root, "research_plan", "test", {}, broken)
                call.assert_not_called()

    def test_mock_responses_call_accepts_complete_research_plan_schema(self):
        plan = {
            "version": 1, "status": "ready", "exact_topic": "MH370", "documentary_angle": None,
            "requested_focus": "MH370", "target_duration_minutes": 8, "video_language": "Nederlands",
            "people": [], "locations": ["Maleisië"], "dates": [], "events": [], "exclusions": [],
            "factual_questions": ["Wat gebeurde er?"],
            "involved_countries": [{"country": "Maleisië", "language": "Bahasa Melayu", "reason": "Vertrekland"}],
            "relevant_languages": ["Bahasa Melayu", "English", "中文"],
            "source_priorities": [{"level": 1, "categories": ["officiële documenten"]}],
            "coverage_targets": [{"country": "Maleisië", "minimum_percentage": 80}],
        }
        captured = {}
        def fake_urlopen(request, timeout=0):
            captured.update(json.loads(request.data.decode()))
            return _Response({"output_text": json.dumps(plan), "usage": {"input_tokens": 1, "output_tokens": 1}})
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), "MH370")
            provider = OpenAIReasoningProvider(ReasoningConfig(enabled=True), api_key="test")
            with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                result = provider.analyze_request(project.root, {"prompt": "MH370", "language": "Nederlands"})
        self.assertEqual(result, plan)
        sent = captured["text"]["format"]["schema"]
        self.assertEqual(set(sent["required"]), set(sent["properties"]))

    def test_failed_project_is_queued_without_losing_approval_or_calling_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), "Recover schema")
            manifests = project.root / "manifests"
            approval = {"confirmed": True, "project": project.root.name, "approved_limit_usd": .01, "operations": ["research_plan"]}
            write_json(manifests / "paid_api_confirmation.json", approval)
            write_json(manifests / "orchestration.json", {"status": "blocked", "current_stage": "research_plan", "last_error": "code: invalid_json_schema"})
            write_json(manifests / "production_plan.json", {"stages": [{"id": "research_plan", "status": "blocked"}]})
            write_json(manifests / "research.json", {"status": "blocked"})
            self.assertTrue(recover_invalid_schema_task(project.root))
            self.assertEqual(read_json(manifests / "paid_api_confirmation.json"), approval)
            self.assertTrue(read_json(manifests / "orchestration.json")["resume_after_restart"])
            app = DashboardApp(Path(temporary)); app.projects = lambda: [project.root]  # type: ignore[method-assign]
            with patch.object(app, "resume_managed_production") as resume, patch("inside_case_factory.web.dashboard.Thread") as thread:
                app.resume_recoverable_projects()
            resume.assert_not_called()
            thread.assert_called_once()


if __name__ == "__main__": unittest.main()
