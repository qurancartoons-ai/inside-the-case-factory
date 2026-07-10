from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.content_modes import CONTENT_MODES, CLAIM_CLASSIFICATIONS, mode_prompt, normalize_content_mode
from inside_case_factory.core.production import ProductionRequest, start_production
from inside_case_factory.config.settings import load_settings
from inside_case_factory.web.dashboard import DashboardApp


class ContentModeTests(unittest.TestCase):
    def test_all_modes_have_dutch_labels_and_distinct_policies(self) -> None:
        self.assertEqual(CONTENT_MODES["factual_documentary"]["label_nl"], "Feitelijke documentaire")
        self.assertEqual(CONTENT_MODES["investigative_documentary"]["label_nl"], "Onderzoeksdocumentaire")
        self.assertEqual(CONTENT_MODES["theory_conspiracy"]["label_nl"], "Theorie / complot")
        self.assertIn("speculation", CONTENT_MODES["theory_conspiracy"]["claim_classes"])
        self.assertNotIn("speculation", CONTENT_MODES["factual_documentary"]["claim_classes"])

    def test_invalid_mode_falls_back_to_factual(self) -> None:
        self.assertEqual(normalize_content_mode("unknown"), "factual_documentary")
        self.assertIn("Never silently upgrade", mode_prompt("theory_conspiracy"))

    def test_production_request_persists_mode_without_running_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "defaults.toml").write_text("[paths]\nprojects_dir='projects'\n[pipeline]\nallow_paid_providers=false\n[review_gates]\n", encoding="utf-8")
            (root / "config" / "providers.toml").write_text("[reasoning]\nenabled=false\n", encoding="utf-8")
            settings = load_settings(root)
            result = start_production(settings, ProductionRequest("Make a theory documentary about a disputed case", content_mode="theory_conspiracy"))
            request = (Path(result["project_root"]) / "manifests" / "production_request.json").read_text()
            workflow = (Path(result["project_root"]) / "manifests" / "workflow.json").read_text()
            self.assertIn("theory_conspiracy", request)
            self.assertIn("theory_conspiracy", workflow)
            self.assertFalse((Path(result["project_root"]) / "manifests" / "script.json").exists())

    def test_dashboard_contains_documentary_mode_selector(self) -> None:
        app = DashboardApp(Path.cwd())
        html = app.index()
        self.assertIn('name="content_mode"', html)
        self.assertIn("Feitelijke documentaire", html)
        self.assertIn("Onderzoeksdocumentaire", html)
        self.assertIn("Theorie / complot", html)


if __name__ == "__main__":
    unittest.main()
