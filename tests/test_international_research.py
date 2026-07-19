from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.international_research import analyze_coverage, build_international_strategy, detect_claim_conflicts, enrich_claim_provenance, filter_and_rank_results
from inside_case_factory.core.project import create_project


class InternationalResearchTests(unittest.TestCase):
    def test_context_and_languages_are_topic_driven(self):
        cases = {
            "Madeleine McCann": ["Verenigd Koninkrijk", "Portugal", "Duitsland"],
            "MH370": ["Maleisië", "Australië", "China"],
            "Titanic": ["Verenigd Koninkrijk", "Verenigde Staten"],
            "Gaza": ["Israël", "Palestina", "Verenigde Naties"],
        }
        for topic, countries in cases.items():
            strategy = build_international_strategy(topic, "Nederlands")
            self.assertEqual([item["country"] for item in strategy["contexts"]], countries)
            self.assertNotEqual(strategy["search_languages"], ["Nederlands"])
            self.assertIn("requires_new_paid_confirmation", strategy["iteration_policy"])

    def test_source_hierarchy_deduplication_and_entertainment_filter(self):
        results = filter_and_rank_results([
            {"title": "Police report", "url": "https://police.uk/report", "quality_score": 5},
            {"title": "Reuters investigation", "url": "https://reuters.com/a", "quality_score": 90},
            {"title": "Netflix film", "url": "https://filmvandaag.nl/netflix", "quality_score": 99},
            {"title": "Duplicate", "url": "https://www.reuters.com/a", "quality_score": 80},
            {"title": "Nederlandse context", "url": "https://nos.nl/context", "quality_score": 80},
        ])
        self.assertEqual([item["source_tier"] for item in results], [1, 3, 5])
        self.assertEqual(len(results), 3)

    def test_claim_provenance_conflicts_and_coverage(self):
        sources = [
            {"id": "police", "url": "https://police.uk/report", "source_tier": 1, "source_country": "Verenigd Koninkrijk", "primary_source": True, "relevance_status": "relevant"},
            {"id": "news", "url": "https://reuters.com/report", "source_tier": 3, "source_country": "Verenigd Koninkrijk", "primary_source": False, "relevance_status": "relevant"},
            {"id": "local", "url": "https://example.pt/report", "source_tier": 4, "source_country": "Portugal", "primary_source": False, "relevance_status": "relevant"},
        ]
        claims = [
            {"id": "c1", "text": "Vaststelling", "source_ids": ["police", "news"], "contradiction_notes": "Bronnen spreken elkaar tegen."},
            {"id": "c2", "text": "Andere lezing", "source_ids": ["local"]},
        ]
        enrich_claim_provenance(claims, sources, "Nederlands"); detect_claim_conflicts(claims)
        self.assertEqual(claims[0]["original_language"], "English")
        self.assertEqual(claims[0]["translated_text"], "Vaststelling")
        self.assertEqual(claims[0]["independent_source_count"], 2)
        self.assertTrue(claims[0]["primary_source"])
        self.assertEqual(claims[0]["most_authoritative_source_id"], "police")
        self.assertEqual(claims[0]["contradicting_source_ids"], ["local"])
        with tempfile.TemporaryDirectory() as temporary:
            root = create_project(Path(temporary), "Coverage").root
            strategy = build_international_strategy("Titanic", "Nederlands")
            report = analyze_coverage(root, strategy, sources, claims)
            self.assertEqual(report["countries"][0]["country"], "Verenigd Koninkrijk")
            self.assertGreater(report["countries"][0]["score"], 0)
            self.assertTrue(report["new_paid_confirmation_required"])


if __name__ == "__main__": unittest.main()
