from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.project import create_project
from inside_case_factory.core.relevance import project_context, rebuild_relevance_cache, topic_relevance
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


ROOT = Path(__file__).resolve().parents[1]


class ReviewRelevanceMediaRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = create_project(Path(self.temp.name), "Michael Jackson death").root
        write_json(self.project / "manifests/research_plan.json", {
            "exact_topic": "The death of Michael Jackson", "people": ["Michael Jackson"],
            "events": ["Death of Michael Jackson", "FBI investigation"], "dates": ["2009"],
            "involved_countries": [{"country": "United States", "language": "English"}],
            "factual_questions": ["What did the FBI investigate?"],
        })
        write_json(self.project / "manifests/sources.json", {"sources": [
            {"id": "fbi", "title": "FBI — Michael Jackson Investigative Files", "url": "https://fbi.gov/michael-jackson", "publisher": "FBI", "source_type": "official_record", "source_tier": 1, "review_status": "pending_review"},
            {"id": "guardian", "title": "Michael Jackson was murdered, says his sister", "url": "https://theguardian.com/music/michael-jackson", "publisher": "The Guardian", "source_type": "news", "source_tier": 3, "review_status": "pending_review"},
        ]})
        write_json(self.project / "manifests/source_snapshots.json", {"snapshots": [
            {"source_id": "fbi", "content": "FBI investigative files concerning Michael Jackson were released in 2009."},
            {"source_id": "guardian", "content": "The Guardian reports allegations concerning Michael Jackson's death."},
        ]})
        write_json(self.project / "manifests/claims.json", {"claims": [{"id": "c1", "text": "A disputed claim", "source_ids": ["guardian"], "review_status": "pending_review"}]})
        self.app = DashboardApp(ROOT)
        self.app.project_root = lambda slug: self.project  # type: ignore[method-assign]

    def tearDown(self):
        self.temp.cleanup()

    def test_source_approval_persists_redirects_to_item_and_survives_restart(self):
        response = self.app.review_research_item(self.project.name, "source", "fbi", "approve")
        self.assertEqual(response[0], "303 See Other")
        self.assertIn("/dossier-review?notice=Bron%20goedgekeurd#source-fbi", dict(response[1])["Location"])
        self.assertEqual(read_json(self.project / "manifests/sources.json")["sources"][0]["review_status"], "approved")
        restarted = DashboardApp(ROOT); restarted.project_root = lambda slug: self.project  # type: ignore[method-assign]
        self.assertIn("Goedgekeurd", restarted.dossier_review_page(self.project.name))

    def test_claim_rejection_persists_and_invalid_id_changes_nothing(self):
        response = self.app.review_research_item(self.project.name, "claim", "c1", "reject")
        self.assertIn("Claim%20afgewezen#claim-c1", dict(response[1])["Location"])
        self.assertEqual(read_json(self.project / "manifests/claims.json")["claims"][0]["review_status"], "rejected")
        before = (self.project / "manifests/claims.json").read_bytes()
        invalid = self.app.review_research_item(self.project.name, "claim", "missing", "approve")
        self.assertEqual(invalid[0], "404 Not Found")
        self.assertEqual(before, (self.project / "manifests/claims.json").read_bytes())

    def test_double_click_is_idempotent_and_dispatch_accepts_mobile_post(self):
        self.app.review_research_item(self.project.name, "source", "fbi", "approve")
        self.app.review_research_item(self.project.name, "source", "fbi", "approve")
        events = read_json(self.project / "manifests/progress_events.json")["events"]
        self.assertEqual(sum(event.get("item_id") == "fbi" for event in events), 1)
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": f"/projects/{self.project.name}/research/claim/c1/reject", "CONTENT_LENGTH": "0", "CONTENT_TYPE": "", "wsgi.input": BytesIO(b"")}
        response = self.app.dispatch(environ)
        self.assertEqual(response[0], "303 See Other")
        self.assertIn("#claim-c1", dict(response[1])["Location"])

    def test_media_id_and_project_slug_are_validated_and_persisted(self):
        image = self.project / "assets/images/preview.jpg"; image.parent.mkdir(parents=True, exist_ok=True); image.write_bytes(b"preview")
        write_json(self.project / "manifests/media_sources.json", {"assets": [{"id": "asset-1", "path": "assets/images/preview.jpg", "review_status": "pending_review", "suggested_scenes": ["s1"]}]})
        seen = []
        self.app.project_root = lambda slug: (seen.append(slug) or self.project)  # type: ignore[method-assign]
        with patch.object(self.app, "resume_managed_production"):
            response = self.app.review_media(self.project.name, "asset-1", "approve")
        self.assertEqual(seen, [self.project.name])
        self.assertIn("#media-asset-1", dict(response[1])["Location"])
        self.assertEqual(read_json(self.project / "manifests/media_sources.json")["assets"][0]["review_status"], "approved")
        before = (self.project / "manifests/media_sources.json").read_bytes()
        invalid = self.app.review_media(self.project.name, "missing", "reject")
        self.assertEqual(invalid[0], "404 Not Found")
        self.assertEqual(before, (self.project / "manifests/media_sources.json").read_bytes())

    def test_fbi_and_guardian_score_high_and_missing_content_is_not_zero(self):
        rebuild_relevance_cache(self.project)
        sources = read_json(self.project / "manifests/sources.json")["sources"]
        self.assertGreaterEqual(sources[0]["topic_relevance"], 0.8)
        self.assertGreaterEqual(sources[1]["topic_relevance"], 0.7)
        result = topic_relevance(project_context(self.project), {"title": "X"})
        self.assertIsNone(result["score"])

    def test_1840_newspaper_scores_zero_and_license_does_not_affect_topic(self):
        context = project_context(self.project)
        base = {"title": "De Avondbode 11-07-1840", "description": "Nederlandse krant over handel en landbouw"}
        public = topic_relevance(context, {**base, "license": "Public domain", "copyright_status": "likely_open"})
        restricted = topic_relevance(context, {**base, "license": "All rights reserved", "copyright_status": "restrictive_or_unknown"})
        self.assertEqual(public["score"], 0)
        self.assertEqual(public["score"], restricted["score"])

    def test_media_filter_deduplicates_excludes_irrelevant_and_cross_project(self):
        image = self.project / "assets/images/preview.jpg"; image.parent.mkdir(parents=True, exist_ok=True); image.write_bytes(b"preview")
        relevant = {"id": "mj", "title": "Michael Jackson FBI files", "description": "Michael Jackson FBI investigation in 2009", "path": "assets/images/preview.jpg", "source_url": "https://fbi.gov/mj", "sha256": "one", "mapped_scenes": ["s1"], "review_status": "pending_review", "copyright_status": "unknown"}
        duplicate = {**relevant, "id": "mj-copy"}
        old = {"id": "old", "title": "De Avondbode 11-07-1840", "description": "Krant over landbouw", "path": "assets/images/preview.jpg", "source_url": "https://example.nl/1840", "sha256": "two", "mapped_scenes": ["s1"], "review_status": "pending_review", "copyright_status": "likely_open"}
        foreign = {**relevant, "id": "foreign", "sha256": "three", "project_slug": "other-project"}
        write_json(self.project / "manifests/media_sources.json", {"assets": [relevant, duplicate, old, foreign]})
        result = rebuild_relevance_cache(self.project)
        self.assertEqual(result["duplicate_assets"], 1)
        html = self.app.review_queue(self.project, self.project.name)
        self.assertEqual(html.count('id="media-mj"'), 1)
        self.assertNotIn('id="media-mj-copy"', html)
        self.assertNotIn('id="media-old"', html)
        self.assertNotIn('id="media-foreign"', html)

    def test_cache_invalidation_and_dutch_labels(self):
        data = read_json(self.project / "manifests/sources.json"); data["sources"][0].update({"relevance_score": 0, "relevance_model_version": 1}); write_json(self.project / "manifests/sources.json", data)
        rebuild_relevance_cache(self.project)
        refreshed = read_json(self.project / "manifests/sources.json")["sources"][0]
        self.assertGreater(refreshed["relevance_score"], 0.8)
        image = self.project / "assets/images/preview.jpg"; image.parent.mkdir(parents=True, exist_ok=True); image.write_bytes(b"preview")
        write_json(self.project / "manifests/media_sources.json", {"assets": [{"id": "mj", "title": "Michael Jackson FBI files", "description": "Michael Jackson FBI investigation", "path": "assets/images/preview.jpg", "source_url": "https://fbi.gov/mj", "mapped_scenes": ["s1"], "suggested_scenes": ["s1"], "review_status": "pending_review", "copyright_status": "likely_open"}]})
        html = self.app.research_panel(self.project, self.project.name) + self.app.review_queue(self.project, self.project.name)
        for label in ("Goedkeuren", "Afwijzen", "Nog te beoordelen", "Voorgestelde scène", "Oorspronkelijke bron"):
            self.assertIn(label, html)
        for english in (">Approve<", ">Reject<", "Suggested:", "Original source", ">pending_review<"):
            self.assertNotIn(english, html)


if __name__ == "__main__":
    unittest.main()
