import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.discovery import DiscoveryQuery, _scene_specific_queries
from inside_case_factory.core.discovery import discover_project_scene_media
from inside_case_factory.core.autonomous_direction import DirectorEngine
from inside_case_factory.core.project import create_project
from inside_case_factory.core.relevance import rebuild_relevance_cache, validate_scene_asset_gate
from inside_case_factory.providers.runtime_media import PexelsStockMediaProvider
from inside_case_factory.providers.visual_assets import resolve_shot_assets
from inside_case_factory.utils.files import read_json, write_json


class FakePexelsTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    def get_json(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.payload


class PexelsProviderIntegrationTests(unittest.TestCase):
    def test_successful_normalization_and_duplicate_removal(self):
        payload = {
            "photos": [
                {
                    "id": 100,
                    "url": "https://www.pexels.com/photo/100/",
                    "alt": "Los Angeles court archive still",
                    "photographer": "A. Photographer",
                    "photographer_url": "https://www.pexels.com/@a",
                    "width": 3000,
                    "height": 2000,
                    "src": {
                        "original": "https://images.pexels.com/photos/100/original.jpg",
                        "large2x": "https://images.pexels.com/photos/100/large2x.jpg",
                    },
                },
                {
                    "id": 100,
                    "url": "https://www.pexels.com/photo/100/",
                    "alt": "Duplicate should be removed",
                    "photographer": "A. Photographer",
                    "photographer_url": "https://www.pexels.com/@a",
                    "width": 3000,
                    "height": 2000,
                    "src": {
                        "original": "https://images.pexels.com/photos/100/original.jpg",
                        "large2x": "https://images.pexels.com/photos/100/large2x.jpg",
                    },
                },
            ]
        }
        provider = PexelsStockMediaProvider(enabled=True, transport=FakePexelsTransport(payload=payload))
        query = DiscoveryQuery(topic="michael jackson los angeles 2009", desired_media_type="image", shot_id="s01-shot-1", limit_per_source=4)

        with patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"}, clear=True):
            results = provider.search(query)

        self.assertEqual(len(results), 1)
        asset = results[0]
        self.assertEqual(asset["provider"], "pexels")
        self.assertTrue(asset["source_url"].startswith("https://www.pexels.com/"))
        self.assertTrue(asset["preview_url"].startswith("https://images.pexels.com/"))
        self.assertEqual(asset["media_type"], "image")
        self.assertIn("title", asset)
        self.assertIn("description", asset)
        self.assertEqual(asset["dimensions"]["width"], 3000)
        self.assertIsNone(asset["duration_seconds"])
        self.assertIn("license_metadata", asset)
        self.assertIn("scene_linkage", asset)
        self.assertEqual(provider.last_status.candidates_returned, 1)

    def test_missing_api_key_fails_gracefully(self):
        provider = PexelsStockMediaProvider(enabled=True, transport=FakePexelsTransport(payload={"photos": []}))
        with patch.dict(os.environ, {}, clear=True):
            results = provider.search(DiscoveryQuery(topic="archival court records"))

        self.assertEqual(results, [])
        self.assertFalse(provider.last_status.attempted)
        self.assertIn("PEXELS_API_KEY", provider.last_status.skipped_reason)

    def test_provider_timeout_reports_clear_error(self):
        provider = PexelsStockMediaProvider(enabled=True, timeout_seconds=3, transport=FakePexelsTransport(error=TimeoutError("timeout")))
        with patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(RuntimeError) as error:
                provider.search(DiscoveryQuery(topic="courtroom 2009"))

        self.assertIn("Pexels request timed out", str(error.exception))
        self.assertTrue(provider.last_status.attempted)
        self.assertIn("timed out", provider.last_status.error_reason)

    def test_provider_error_reports_clear_message(self):
        provider = PexelsStockMediaProvider(enabled=True, transport=FakePexelsTransport(error=RuntimeError("HTTP 503 upstream")))
        with patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(RuntimeError) as error:
                provider.search(DiscoveryQuery(topic="los angeles police"))

        self.assertIn("Pexels provider error", str(error.exception))
        self.assertTrue(provider.last_status.attempted)

    def test_scene_specific_query_generation_is_deduplicated_and_not_full_narration(self):
        scene = {
            "media_requirements": "Need documentary evidence from 2009 courthouse environment.",
            "narration": "Michael Jackson arrives at the Los Angeles courthouse in 2009 while witnesses and legal teams move through the hallways. "
            "This sentence should never be sent verbatim as a provider query because it is too long and contains full narration.",
        }
        intent = {
            "subject": "Michael Jackson investigation",
            "people": ["Michael Jackson"],
            "locations": ["Los Angeles"],
            "time_period": ["2009"],
            "event": ["court hearing"],
            "search_terms": ["Michael Jackson court hearing", "Michael Jackson court hearing"],
            "aliases": ["LA courthouse archive"],
            "content_reason": "Visual evidence for courtroom sequence",
        }

        queries = _scene_specific_queries(scene, intent)

        self.assertGreaterEqual(len(queries), 4)
        self.assertEqual(len(queries), len({item.lower() for item in queries}))
        self.assertTrue(any("los angeles" in item.lower() for item in queries))
        self.assertTrue(any("2009" in item.lower() for item in queries))
        self.assertFalse(any("this sentence should never be sent verbatim" in item.lower() for item in queries))

    def test_semantic_ranking_penalizes_stock_below_archival(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Michael Jackson case").root
            image_dir = project / "assets" / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / "archival.jpg").write_bytes(b"archival")
            (image_dir / "stock.jpg").write_bytes(b"stock")

            write_json(project / "manifests" / "research_plan.json", {
                "exact_topic": "The death of Michael Jackson",
                "people": ["Michael Jackson"],
                "events": ["court hearing"],
                "dates": ["2009"],
            })
            write_json(project / "manifests" / "scenes.json", {
                "scenes": [{
                    "id": "s01",
                    "heading": "Court hearing",
                    "visual_summary": "Michael Jackson court hearing Los Angeles 2009",
                    "people": ["Michael Jackson"],
                    "locations": ["Los Angeles"],
                    "events": ["court hearing"],
                    "dates": ["2009"],
                }]
            })
            write_json(project / "manifests" / "media_sources.json", {
                "version": 1,
                "assets": [
                    {
                        "id": "archival-1",
                        "title": "Archival courtroom footage",
                        "description": "Archival footage of Michael Jackson court hearing in Los Angeles 2009",
                        "path": "assets/images/archival.jpg",
                        "source_url": "https://archive.example.com/a",
                        "review_status": "approved",
                        "copyright_status": "public_domain",
                        "source_category": "archival_footage",
                        "mapped_scenes": ["s01"],
                        "shot_ids": ["s01-shot-1"],
                    },
                    {
                        "id": "stock-1",
                        "title": "Stock courtroom b-roll",
                        "description": "Generic stock footage of city courthouse hallways",
                        "path": "assets/images/stock.jpg",
                        "source_url": "https://www.pexels.com/photo/stock-1/",
                        "review_status": "approved",
                        "copyright_status": "licensed",
                        "source_category": "generic_stock_footage",
                        "discovery": {"source": "pexels"},
                        "mapped_scenes": ["s01"],
                        "shot_ids": ["s01-shot-1"],
                    },
                ],
            })

            rebuild_relevance_cache(project)
            assets = read_json(project / "manifests" / "media_sources.json")["assets"]
            archival = next(item for item in assets if item["id"] == "archival-1")
            stock = next(item for item in assets if item["id"] == "stock-1")

            self.assertGreater(archival["source_policy_score"], stock["source_policy_score"])
            self.assertTrue(archival["archival_priority"])
            self.assertFalse(stock["archival_priority"])

    def test_asset_gate_accepts_relevant_stock_when_scene_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Gate case").root
            scene = {
                "id": "s01",
                "heading": "Court hearing evidence",
                "archival_media_queries": ["Michael Jackson court hearing Los Angeles 2009"],
                "shots": [{"id": "s01-shot-1", "asset": {"id": "pex-1"}}],
            }
            asset = {
                "id": "pex-1",
                "title": "Stock courtroom still",
                "description": "Michael Jackson court hearing Los Angeles 2009 documentary context",
                "source_url": "https://www.pexels.com/photo/pex-1/",
                "review_eligible": True,
                "relevance_score": 0.85,
                "review_status": "approved",
                "source_category": "generic_stock_footage",
                "mapped_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "project_slug": project.name,
            }

            result = validate_scene_asset_gate(project, [scene], media_assets=[asset], threshold=0.35)
            self.assertTrue(result["passed"])
            self.assertEqual(result["results"][0]["accepted_asset_ids"], ["pex-1"])

    def test_ai_fallback_only_when_no_suitable_pexels_candidate_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Fallback case").root
            image_dir = project / "assets" / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / "pexels.jpg").write_bytes(b"pexels")
            scene = {
                "id": "s01",
                "heading": "Court hearing",
                "claim_ids": [],
            }

            suitable = {
                "id": "pexels-ok",
                "type": "image",
                "path": "assets/images/pexels.jpg",
                "source_url": "https://www.pexels.com/photo/ok/",
                "license": "Pexels License",
                "rights_status": "licensed",
                "review_status": "approved",
                "mapped_scenes": ["s01"],
                "shot_ids": ["s01-shot-1"],
                "relevance_score": 0.9,
            }
            selected = resolve_shot_assets(project, scene, "s01-shot-1", [suitable], desired_media_type="image")
            self.assertFalse(selected[0].get("generated", False))
            self.assertEqual(selected[0]["id"], "pexels-ok")

            unsuitable = {
                **suitable,
                "id": "pexels-pending",
                "review_status": "pending_review",
            }
            fallback_only = resolve_shot_assets(project, scene, "s01-shot-1", [unsuitable], desired_media_type="image")
            self.assertTrue(all(item.get("generated", False) for item in fallback_only))

    def test_discovery_provider_availability_reports_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Provider availability").root
            scene = {
                "id": "s01",
                "heading": "Court hearing",
                "narration": "A documented courtroom sequence in Los Angeles.",
                "visual_summary": "court hearing evidence",
                "duration_seconds": 6.0,
                "claim_ids": ["c1"],
                "people": ["Michael Jackson"],
                "locations": ["Los Angeles"],
                "events": ["court hearing"],
                "dates": ["2009"],
                "archival_media_queries": ["Michael Jackson court hearing Los Angeles 2009"],
                "alternative_media_queries": ["Los Angeles courthouse archive"],
                "media_requirements": "Visual evidence of hearing context",
            }
            write_json(project / "manifests" / "scenes.json", {"scenes": [scene]})
            DirectorEngine().plan(project, [scene], width=1920, height=1080)

            provider = PexelsStockMediaProvider(enabled=True, transport=FakePexelsTransport(payload={"photos": []}))
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError):
                    discover_project_scene_media(project, connectors=[provider], limit_per_source=1)

            discovery = read_json(project / "manifests" / "media_discovery.json")
            report = discovery["provider_availability"]["pexels"]
            self.assertTrue(report["configured"])
            self.assertFalse(report["key_available"])
            self.assertFalse(report["attempted"])
            self.assertEqual(report["candidates_returned"], 0)
            self.assertIn("PEXELS_API_KEY", report["skipped_reason"])


if __name__ == "__main__":
    unittest.main()
