import unittest

from inside_case_factory.core.depth_research import (
    FOCUSED_QUERIES,
    RESEARCH_QUESTIONS,
    build_coverage,
    extract_atomic_sentence_proposals,
    merge_semantically_equivalent_proposals,
    select_authoritative_results,
)


class DepthResearchTests(unittest.TestCase):
    def test_every_question_has_multiple_focused_query_routes(self) -> None:
        counts = {question["id"]: 0 for question in RESEARCH_QUESTIONS}
        for query in FOCUSED_QUERIES:
            for question_id in query["question_ids"]:
                counts[question_id] += 1
        self.assertTrue(all(count >= 2 for count in counts.values()))

    def test_source_selection_excludes_unidentified_video_and_blog(self) -> None:
        results = [
            {"url": "https://example.gov/report", "source_type": "official_record", "quality_score": 100, "score": 1},
            {"url": "https://youtube.com/watch?v=x", "title": "Random recap", "source_type": "video", "quality_score": 90, "score": 1},
            {"url": "https://law.example/blog", "source_type": "blog", "quality_score": 80, "score": 1},
        ]
        selected = select_authoritative_results(results)
        self.assertEqual([item["url"] for item in selected], ["https://example.gov/report"])

    def test_semantic_merge_combines_evidence_for_corroboration_validation(self) -> None:
        proposals = [
            {"canonical_key": "official-cause", "text": "Cause A", "source_ids": ["s1"], "evidence": [{"source_id": "s1"}]},
            {"canonical_key": "official-cause", "text": "Cause A", "source_ids": ["s2"], "evidence": [{"source_id": "s2"}]},
        ]
        merged = merge_semantically_equivalent_proposals(proposals)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_ids"], ["s1", "s2"])
        self.assertEqual(len(merged[0]["evidence"]), 2)

    def test_coverage_marks_unanswered_questions_as_gaps(self) -> None:
        coverage = build_coverage(RESEARCH_QUESTIONS[:1], [], [], [])
        self.assertTrue(coverage["requires_more_search"])
        self.assertTrue(coverage["questions"][0]["gaps"])

    def test_atomic_sentence_claim_is_exact_stored_evidence(self) -> None:
        sentence = "The coroner concluded that the manner of death was homicide after reviewing the evidence."
        proposals = extract_atomic_sentence_proposals([{"source_id": "s1", "content": sentence}])
        self.assertEqual(proposals[0]["text"], sentence)
        self.assertEqual(proposals[0]["evidence"][0]["exact_excerpt"], sentence)


if __name__ == "__main__":
    unittest.main()
