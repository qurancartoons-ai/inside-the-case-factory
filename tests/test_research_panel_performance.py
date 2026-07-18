from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from inside_case_factory.core.project import create_project
from inside_case_factory.core.research import review_item
from inside_case_factory.core.research_panel import ResearchPanelService
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp


class ResearchPanelPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Research Performance").root
        self.sources = [{
            "id": f"s{i}", "title": f"Source {i}", "url": f"https://example.invalid/{i}",
            "publisher": "Archive", "review_status": "pending_review", "transcript": "transcript-word " * 500,
            "attachments": [{"url": f"https://example.invalid/thumb-{i}.jpg", "title": "Screenshot"}],
        } for i in range(1000)]
        self.claims = [{"id": f"c{i}", "text": "Evidence claim " + "detail " * 20, "source_ids": [f"s{i}"], "review_status": "pending_review"} for i in range(1000)]
        write_json(self.root / "manifests/sources.json", {"version": 1, "sources": self.sources})
        write_json(self.root / "manifests/claims.json", {"version": 1, "claims": self.claims})
        self.app = DashboardApp(Path.cwd())

    def tearDown(self):
        self.temporary.cleanup()

    def test_panel_shell_performs_no_provider_network_or_manifest_call(self):
        original = Path.read_text; count = []
        def counted(path, *args, **kwargs): count.append(path); return original(path, *args, **kwargs)
        with patch("urllib.request.urlopen") as network, patch.object(Path, "read_text", counted):
            html = self.app.research_panel(self.root, "research-performance")
        network.assert_not_called()
        self.assertEqual(len(count), 0)
        self.assertLess(len(html.encode()), 15000)
        self.assertIn("Researchoverzicht laden", html)

    def test_large_sources_and_claims_are_paginated(self):
        service = ResearchPanelService(self.root)
        sources = service.page("sources", 2, 25)
        claims = service.page("claims", 40, 25)
        self.assertEqual(sources.total, 1000)
        self.assertEqual(len(sources.items), 25)
        self.assertEqual(sources.items[0]["id"], "s25")
        self.assertEqual(len(claims.items), 25)
        self.assertLess(len(str(sources.payload())), 20000)

    def test_each_data_request_reads_requested_manifest_at_most_once(self):
        service = ResearchPanelService(self.root)
        original = Path.read_text
        paths = []
        def counted(path, *args, **kwargs):
            paths.append(str(path)); return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", counted):
            service.page("sources", 1, 25)
        source_reads = [path for path in paths if path.endswith("manifests/sources.json")]
        self.assertEqual(len(source_reads), 1)
        self.assertNotIn("manifests/claims.json", " ".join(paths))

    def test_transcript_is_previewed_and_full_text_loads_only_on_request(self):
        service = ResearchPanelService(self.root)
        page = service.page("sources", 1, 25)
        self.assertLessEqual(len(page.items[0]["transcript_preview"]), 241)
        self.assertNotIn("transcript-word " * 20, str(page.payload()))
        transcript = service.transcript("s0", limit=2000)
        self.assertEqual(len(transcript["text"]), 2000)
        self.assertTrue(transcript["has_more"])

    def test_cache_reuse_and_mtime_size_invalidation(self):
        service = ResearchPanelService(self.root)
        first = service.page("sources", 1, 25)
        second = service.page("sources", 1, 25)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        data = read_json(self.root / "manifests/sources.json")
        data["sources"][0]["title"] = "Changed source title with different size"
        write_json(self.root / "manifests/sources.json", data)
        invalidated = service.page("sources", 1, 25)
        self.assertFalse(invalidated.cache_hit)
        self.assertEqual(invalidated.items[0]["title"], "Changed source title with different size")

    def test_heavy_analysis_is_only_queued_and_deduplicated(self):
        service = ResearchPanelService(self.root)
        with patch("inside_case_factory.providers.reasoning.reasoning_provider_from_settings", Mock()) as provider:
            first = service.queue_analysis("Investigate discrepancy")
            second = service.queue_analysis("Investigate discrepancy")
        provider.assert_not_called()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["provider_calls"], 0)

    def test_lazy_attachment_markup_and_transcript_button_are_present(self):
        html = self.app.research_panel(self.root, "research-performance")
        self.assertIn('loading="lazy"', html)
        self.assertIn("Transcript laden", html)
        self.assertIn("requestAnimationFrame", html)

    def test_heavy_sections_fetch_only_after_the_user_opens_them(self):
        html = self.app.research_panel(self.root, "research-performance")
        self.assertIn('data-research-section="sources"', html)
        self.assertIn('data-research-section="claims"', html)
        self.assertIn("section.addEventListener('toggle'", html)
        self.assertNotIn("requestAnimationFrame(()=>{load('sources');load('claims');})", html)

    def test_pagination_keeps_every_page_reachable(self):
        html = self.app.research_panel(self.root, "research-performance")
        self.assertIn("data.page+1", html)
        self.assertIn("data.page-1", html)
        self.assertNotIn("Math.min(pages,20)", html)

    def test_existing_approval_and_provenance_remain_unchanged(self):
        review_item(self.root, "claims.json", "claims", "c0", "approved")
        claim = read_json(self.root / "manifests/claims.json")["claims"][0]
        self.assertEqual(claim["review_status"], "approved")
        self.assertEqual(claim["source_ids"], ["s0"])
        page = ResearchPanelService(self.root).page("claims", 1, 25)
        self.assertEqual(page.items[0]["review_status"], "approved")
        self.assertEqual(page.items[0]["source_ids"], ["s0"])


if __name__ == "__main__":
    unittest.main()
