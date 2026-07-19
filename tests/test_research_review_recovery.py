from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.research import analyse_research_review, approve_research, review_item
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


ROOT = Path(__file__).resolve().parents[1]


class ResearchReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temp.name), "Madeleine McCann").root
        write_json(self.root / "manifests/production_request.json", {"topic": "Madeleine McCann"})
        relevant = {"id": "vrt", "title": "Zaak-Madeleine McCann", "url": "https://vrt.be/mccann", "publisher": "VRT", "review_status": "pending_review"}
        duplicate = {**relevant, "id": "vrt-copy", "url": "https://www.vrt.be/mccann"}
        irrelevant = {"id": "passport", "title": "Vermissing van een reisdocument", "url": "https://rvig.nl/paspoort", "publisher": "RvIG", "review_status": "pending_review"}
        netflix = {"id": "netflix", "title": "Netflix-film over Elizabeth", "url": "https://film.nl/elizabeth", "publisher": "Film", "review_status": "pending_review"}
        sources = [relevant, duplicate, irrelevant, netflix] + [{"id": f"other-{i}", "title": f"Andere vermissing {i}", "url": f"https://example.nl/{i}", "publisher": "Example", "review_status": "pending_review"} for i in range(4)]
        write_json(self.root / "manifests/sources.json", {"version": 1, "sources": sources})
        write_json(self.root / "manifests/claims.json", {"version": 1, "claims": []})
        write_json(self.root / "manifests/source_snapshots.json", {"version": 1, "snapshots": [{"source_id": "vrt", "content": "Madeleine McCann verdween in mei 2007 uit een vakantieappartement in Praia da Luz. Volgens onderzoekers zou de zaak-Madeleine McCann nog altijd niet zijn opgelost."}]})
        self.app = DashboardApp(ROOT); self.app.project_root = lambda slug: self.root  # type: ignore[method-assign]

    def tearDown(self): self.temp.cleanup()

    def test_eight_sources_zero_claims_has_recovery_actions(self):
        html = self.app.dossier_review_page("madeleine-mccann")
        self.assertIn("Er zijn nog geen controleerbare feiten opgesteld", html)
        self.assertIn("Claims uit relevante bronnen opstellen", html)
        self.assertIn("Onderzoek verder", html)
        self.assertIn("minimaal één goedgekeurde relevante bron", html)

    def test_filter_deduplicates_rejects_irrelevant_and_extracts_linked_claims(self):
        result = analyse_research_review(self.root)
        self.assertEqual(result["sources"], 8); self.assertEqual(result["relevant"], 1); self.assertEqual(result["duplicates"], 1)
        sources = read_json(self.root / "manifests/sources.json")["sources"]
        self.assertEqual(next(s for s in sources if s["id"] == "passport")["relevance_status"], "irrelevant")
        self.assertEqual(next(s for s in sources if s["id"] == "vrt-copy")["relevance_status"], "duplicate")
        claims = read_json(self.root / "manifests/claims.json")["claims"]
        self.assertGreaterEqual(len(claims), 1)
        self.assertTrue(all(c["source_ids"] == ["vrt"] for c in claims))
        attributed = next(c for c in claims if "zou" in c["text"].lower())
        self.assertTrue(attributed["text"].startswith("Volgens VRT:"))

    def test_dutch_statuses_and_approval_requires_linked_approved_items(self):
        analyse_research_review(self.root)
        html = self.app.dossier_review_page("madeleine-mccann")
        self.assertIn("Te beoordelen", html); self.assertNotIn(">pending_review<", html)
        review_item(self.root, "sources.json", "sources", "vrt", "approved")
        claim_id = read_json(self.root / "manifests/claims.json")["claims"][0]["id"]
        review_item(self.root, "claims.json", "claims", claim_id, "approved")
        self.assertTrue(approve_research(self.root))
        html = self.app.dossier_review_page("madeleine-mccann")
        self.assertIn("Klaar om goed te keuren", html)
        self.assertNotIn("button disabled", html)


if __name__ == "__main__": unittest.main()
