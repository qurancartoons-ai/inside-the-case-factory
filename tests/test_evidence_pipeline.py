from pathlib import Path
import tempfile
import unittest

from inside_case_factory.core.project import create_project
from inside_case_factory.core.research import (
    build_source_snapshots,
    build_validated_research_artifacts,
    clean_extracted_text,
    estimate_tavily_extract_credits,
    validate_and_store_claims,
)
from inside_case_factory.utils.files import read_json


class EvidencePipelineTests(unittest.TestCase):
    def test_cleaning_removes_furniture_and_duplicates(self) -> None:
        text = "Cookie policy\nThis is a sufficiently long factual sentence about the hearing.\nThis is a sufficiently long factual sentence about the hearing.\nAdvertisement"
        clean = clean_extracted_text(text)
        self.assertEqual(clean, "This is a sufficiently long factual sentence about the hearing.")

    def test_basic_extract_credit_estimate_is_capped_at_eight_urls(self) -> None:
        self.assertEqual(estimate_tavily_extract_credits(5), 1)
        self.assertEqual(estimate_tavily_extract_credits(8), 2)
        self.assertEqual(estimate_tavily_extract_credits(20), 2)

    def test_snapshot_records_hash_method_and_content(self) -> None:
        sources = [{"id": "s1", "url": "https://example.gov/a"}]
        snapshots = build_source_snapshots(sources, {"results": [{"url": "https://example.gov/a", "raw_content": "The official hearing took place in Example City on Tuesday."}]}, [])
        self.assertEqual(snapshots[0]["extraction_method"], "tavily_extract_basic")
        self.assertEqual(len(snapshots[0]["content_hash"]), 64)
        self.assertIn("official hearing", snapshots[0]["content"])

    def test_unmatched_evidence_is_rejected_and_single_source_not_corroborated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Evidence Test")
            snapshots = [{"source_id": "s1", "url": "https://example.gov/a", "content_hash": "a", "content": "The hearing took place in Example City on Tuesday."}]
            proposed = [
                {"text": "Valid", "evidence": [{"source_id": "s1", "exact_excerpt": "The hearing took place in Example City on Tuesday."}], "confidence": "high", "corroboration_status": "corroborated", "people": [], "locations": [], "events": [], "dates": [], "relevance_score": 1, "source_quality": "high", "contradiction_notes": ""},
                {"text": "Invented", "evidence": [{"source_id": "s1", "exact_excerpt": "This text does not exist."}], "confidence": "high", "people": [], "locations": [], "events": [], "dates": [], "relevance_score": 1, "source_quality": "high", "contradiction_notes": ""},
            ]
            claims, rejected = validate_and_store_claims(project.root, proposed, snapshots)
            build_validated_research_artifacts(project.root, claims)
            self.assertEqual(len(claims), 1)
            self.assertEqual(len(rejected), 1)
            self.assertEqual(claims[0]["corroboration_status"], "single_source")
            dossier = read_json(project.root / "manifests" / "dossier.json")
            self.assertEqual(dossier["validated_claim_ids"], [claims[0]["id"]])
            self.assertEqual(dossier["key_facts"][0]["claim_id"], claims[0]["id"])

    def test_duplicate_or_same_domain_sources_are_not_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Duplicate Test")
            excerpt = "The official hearing took place in Example City on Tuesday."
            snapshots = [
                {"source_id": "s1", "url": "https://news.example/a", "content_hash": "same", "content": excerpt},
                {"source_id": "s2", "url": "https://news.example/b", "content_hash": "same", "content": excerpt},
            ]
            proposed = [{"text": "Claim", "evidence": [{"source_id": "s1", "exact_excerpt": excerpt}, {"source_id": "s2", "exact_excerpt": excerpt}], "confidence": "high", "people": [], "locations": [], "events": [], "dates": [], "relevance_score": 1, "source_quality": "high", "contradiction_notes": ""}]
            claims, _ = validate_and_store_claims(project.root, proposed, snapshots)
            self.assertEqual(claims[0]["corroboration_status"], "single_source")


if __name__ == "__main__":
    unittest.main()
