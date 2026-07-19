from pathlib import Path
from io import BytesIO
import os
import json
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.project import available_project_slug, create_project
from inside_case_factory.core.production import ProductionRequest, start_production
from inside_case_factory.core.discovery import DiscoveryQuery, discover_archival_media, discover_project_scene_media
from inside_case_factory.core.autonomous_direction import DirectorEngine
from inside_case_factory.core.media import add_image_asset, image_for_scene, load_media_manifest
from inside_case_factory.core.media import update_image_review
from inside_case_factory.core.research import (
    TavilyResearchProvider,
    add_claim,
    add_source,
    approve_research,
    approve_script,
    generate_scenes,
    generate_script,
    rank_research_results,
    review_item,
    save_script_edit,
)
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.config.env import load_dotenv
from inside_case_factory.config.settings import load_settings
from inside_case_factory.pipeline.generator import _approved_project, _select_voice_provider
from inside_case_factory.pipeline.stages import describe_pipeline
from inside_case_factory.providers.elevenlabs import (
    DEFAULT_MODEL_ID,
    ElevenLabsConfig,
    ElevenLabsVoiceOverProvider,
    elevenlabs_config_from_settings,
)
from inside_case_factory.providers.reasoning import (
    OpenAIReasoningProvider,
    ReasoningConfig,
    estimate_reasoning_cost,
    reasoning_config_from_settings,
)
from inside_case_factory.web.dashboard import DashboardApp


class ProjectScaffoldTests(unittest.TestCase):
    def test_low_cost_strategy_has_stage_models_and_stays_under_budget(self) -> None:
        settings = load_settings(Path.cwd())
        config = reasoning_config_from_settings(settings.providers["reasoning"])
        estimate = estimate_reasoning_cost(config)

        self.assertTrue(config.enabled)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.per_project_spending_limit_usd, 0.25)
        self.assertTrue(config.require_explicit_confirmation)
        self.assertNotEqual(config.stage("source_analysis")["model"], "gpt-5.5")
        self.assertEqual(config.stage("source_analysis")["model"], "gpt-4.1-nano")
        self.assertEqual(config.stage("script")["model"], "gpt-4.1-mini")
        self.assertTrue(estimate["within_budget"])

    def test_create_project_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "A Test Case")

            self.assertEqual(project.slug, "a-test-case")
            self.assertTrue(project.manifest_path.exists())
            self.assertTrue((project.root / "manifests" / "media_sources.json").exists())
            self.assertTrue((project.root / "manifests" / "sources.json").exists())
            self.assertTrue((project.root / "manifests" / "research.json").exists())
            self.assertTrue((project.root / "manifests" / "timeline.json").exists())
            self.assertTrue((project.root / "manifests" / "claims.json").exists())
            self.assertTrue((project.root / "research").is_dir())
            self.assertTrue((project.root / "assets" / "images").is_dir())

    def test_new_project_slug_never_reuses_an_existing_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            first = create_project(projects, "Zelfde onderwerp")
            write_json(first.root / "manifests/claims.json", {"claims": [{"id": "user-data"}]})
            second_slug = available_project_slug(projects, "Zelfde onderwerp")
            self.assertEqual(second_slug, "zelfde-onderwerp-2")
            self.assertEqual(read_json(first.root / "manifests/claims.json")["claims"][0]["id"], "user-data")

    def test_pipeline_has_review_gates(self) -> None:
        stages = describe_pipeline()

        self.assertEqual(stages[0]["kind"], "topic")
        self.assertTrue(any(stage["requires_review"] for stage in stages))
        self.assertTrue(any(stage["expensive"] for stage in stages))

    def test_factual_render_requires_script_scene_and_media_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Approved Case")
            write_json(project.root / "manifests" / "script.json", {
                "version": 1, "title": "Approved Case", "status": "draft", "narration": "Feitelijke tekst."
            })
            write_json(project.root / "manifests" / "scenes.json", {
                "version": 1, "scenes": [{"id": "s01", "narration": "Feitelijke tekst."}]
            })

            with self.assertRaisesRegex(RuntimeError, "script must be explicitly approved"):
                _approved_project(project.root)

    def test_factual_render_accepts_only_fully_reviewed_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Approved Case")
            write_json(project.root / "manifests" / "script.json", {
                "version": 1, "title": "Approved Case", "status": "approved", "narration": "Feitelijke tekst."
            })
            workflow = read_json(project.root / "manifests" / "workflow.json")
            workflow.update({"script_approved": True, "scenes_generated": True})
            write_json(project.root / "manifests" / "workflow.json", workflow)
            write_json(project.root / "manifests" / "scenes.json", {
                "version": 1, "scenes": [{"id": "s01", "narration": "Feitelijke tekst."}]
            })
            write_json(project.root / "manifests" / "media_sources.json", {
                "version": 1,
                "assets": [
                    {"id": "image-1", "review_status": "approved"},
                    {"id": "image-2", "review_status": "rejected"},
                ],
            })

            loaded_project, script, scenes = _approved_project(project.root)

            self.assertEqual(loaded_project.slug, project.slug)
            self.assertEqual(script["status"], "approved")
            self.assertEqual([scene["id"] for scene in scenes], ["s01"])

    def test_voice_provider_falls_back_without_api_key(self) -> None:
        settings = load_settings(Path.cwd())
        provider, label = _select_voice_provider(settings)

        self.assertEqual(provider.name, "ffmpeg_flite")
        self.assertEqual(label, "FFmpeg Flite TTS")

    def test_elevenlabs_defaults_to_v3_and_preserves_voice(self) -> None:
        config = elevenlabs_config_from_settings({"enabled": True})

        self.assertEqual(DEFAULT_MODEL_ID, "eleven_v3")
        self.assertEqual(config.model_id, "eleven_v3")
        self.assertEqual(config.voice_id, "JBFqnCBsd6RMkjVDRZzb")

    def test_elevenlabs_model_remains_configurable(self) -> None:
        config = elevenlabs_config_from_settings(
            {"enabled": True, "model_id": "eleven_multilingual_v2"}
        )

        self.assertEqual(config.model_id, "eleven_multilingual_v2")

    def test_elevenlabs_request_keeps_v3_audio_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured: dict[str, object] = {}
            provider = ElevenLabsVoiceOverProvider(
                ElevenLabsConfig(enabled=True, model_id="eleven_v3"),
                api_key="test-key",
            )

            def fake_request(method: str, url: str, payload: dict[str, object] | None = None) -> bytes:
                captured["method"] = method
                captured["url"] = url
                captured["payload"] = payload
                return b"fake mp3 bytes"

            text = "[whispers] Inside the Case begins... [sighs] then the evidence shifts."
            output = Path(tmp) / "sample.wav"
            text_path = Path(tmp) / "sample.txt"

            with patch.object(provider, "_request", side_effect=fake_request), patch(
                "inside_case_factory.providers.elevenlabs.subprocess.run"
            ):
                provider.synthesize_to_file(text, output, text_path)

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload["text"], text)
            self.assertIn("[whispers]", payload["text"])
            self.assertIn("[sighs]", payload["text"])
            self.assertEqual(payload["model_id"], "eleven_v3")
            self.assertIn("/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb", str(captured["url"]))
            self.assertEqual(text_path.read_text(encoding="utf-8"), text)

    def test_add_image_asset_records_source_metadata_and_scene_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = create_project(tmp_path, "A Test Case")
            source_image = tmp_path / "sample-photo.jpg"
            source_image.write_bytes(b"fake image bytes")

            asset = add_image_asset(
                project.root,
                source_image,
                source_url="https://example.test/photo",
                credit="Example Archive",
                license_notes="Used with permission for test coverage.",
                usage_notes="Manual local photo import.",
                scene_relevance="Shows the location discussed in the cold open.",
                scene_ids=["s01"],
            )

            manifest = load_media_manifest(project.root)
            mapped = image_for_scene(project.root, "s01")

            self.assertEqual(len(manifest["assets"]), 1)
            self.assertEqual(asset["type"], "image")
            self.assertEqual(asset["source_url"], "https://example.test/photo")
            self.assertEqual(asset["credit"], "Example Archive")
            self.assertEqual(asset["mapped_scenes"], ["s01"])
            self.assertTrue((project.root / asset["path"]).exists())
            self.assertIsNotNone(mapped)
            self.assertEqual(mapped["path"], asset["path"])

    def test_unmapped_scene_has_no_real_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = create_project(tmp_path, "A Test Case")
            source_image = tmp_path / "sample-photo.jpg"
            source_image.write_bytes(b"fake image bytes")

            add_image_asset(project.root, source_image, scene_ids=["s01"])

            self.assertIsNone(image_for_scene(project.root, "s02"))

    def test_dashboard_index_loads(self) -> None:
        app = DashboardApp(Path.cwd())
        status_headers: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            status_headers["status"] = status
            status_headers["headers"] = headers

        body = b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/",
                    "QUERY_STRING": "",
                    "wsgi.input": BytesIO(b""),
                    "CONTENT_LENGTH": "0",
                },
                start_response,
            )
        ).decode("utf-8")

        self.assertEqual(status_headers["status"], "200 OK")
        self.assertIn("Nieuwe video maken", body)
        self.assertIn("Beschrijf de video die je wilt maken", body)
        self.assertIn("Videotaal", body)
        self.assertIn("Gewenste lengte", body)
        self.assertIn("Werkwijze", body)
        self.assertIn("Productie starten", body)
        self.assertIn("Projecten", body)

    def test_discovery_workflow_requires_review_before_render_use(self) -> None:
        class FakeConnector:
            name = "fake_archive"

            def search(self, query: DiscoveryQuery) -> list[dict[str, object]]:
                return [
                    {
                        "source": self.name,
                        "source_id": "one",
                        "title": "Example Jane bus shelter",
                        "creator": "Example Archive",
                        "date": "1999",
                        "license": "CC-BY",
                        "attribution_requirements": "Credit Example Archive",
                        "usage_notes": "Open license test fixture.",
                        "source_url": "https://example.test/one",
                        "preview_url": "https://example.test/one.jpg",
                        "description": "Bus shelter near the last known trail.",
                        "copyright_status": "likely_open",
                        "provider_metadata": {},
                    },
                    {
                        "source": self.name,
                        "source_id": "two",
                        "title": "Duplicate bus shelter",
                        "creator": "Example Archive",
                        "date": "1999",
                        "license": "CC-BY",
                        "attribution_requirements": "Credit Example Archive",
                        "usage_notes": "Open license test fixture.",
                        "source_url": "https://example.test/two",
                        "preview_url": "https://example.test/two.jpg",
                        "description": "Bus shelter duplicate.",
                        "copyright_status": "likely_open",
                        "provider_metadata": {},
                    },
                ]

        def fake_download(url: str, path: Path) -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"same preview bytes")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Example Jane")
            write_json(
                project.root / "manifests" / "scenes.json",
                {
                    "scenes": [
                        {
                            "id": "s01",
                            "heading": "The last known trail",
                            "narration": "A bus shelter and a corner store camera define the trail.",
                            "visual_summary": "bus shelter street archive photo",
                        }
                    ]
                },
            )

            with patch("inside_case_factory.core.discovery._download", side_effect=fake_download):
                result = discover_archival_media(
                    project.root,
                    DiscoveryQuery(topic="Example Jane bus shelter", limit_per_source=2),
                    connectors=[FakeConnector()],
                )

            manifest = load_media_manifest(project.root)
            assets = manifest["assets"]

            self.assertEqual(result["added_count"], 1)
            self.assertEqual(result["duplicate_count"], 1)
            self.assertEqual(assets[0]["review_status"], "pending_review")
            self.assertEqual(assets[0]["suggested_scenes"], ["s01"])
            self.assertEqual(len(assets), 1)
            self.assertIsNone(image_for_scene(project.root, "s01"))

            update_image_review(project.root, assets[0]["id"], "approved")
            selected = image_for_scene(project.root, "s01")

            self.assertIsNotNone(selected)
            self.assertEqual(selected["id"], assets[0]["id"])

    def test_project_discovery_searches_per_shot_and_persists_intent(self) -> None:
        class ShotConnector:
            name = "shot_archive"

            def search(self, query):
                return [{
                    "source": self.name, "source_id": f"{query.shot_id}-{index}",
                    "title": f"Example Jane documented event {index}", "creator": "Archive", "date": "2001",
                    "license": "CC-BY", "source_url": f"https://example.test/{query.shot_id}/{index}",
                    "preview_url": f"https://example.test/{query.shot_id}/{index}.jpg", "description": "Example Jane documented event evidence",
                    "copyright_status": "likely_open", "provider_metadata": {},
                } for index in range(2)]

        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Example Jane")
            scene = {"id": "s01", "heading": "Documented event", "narration": "Example Jane documented event.", "duration_seconds": 6.0, "claim_ids": ["c1"], "people": ["Example Jane"], "events": ["documented event"], "archival_media_queries": ["Example Jane documented event"]}
            write_json(project.root / "manifests/scenes.json", {"scenes": [scene]})
            DirectorEngine().plan(project.root, [scene], width=1920, height=1080)

            def download(url: str, path: Path) -> bool:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((b"\x00" * 1024) if "/0.jpg" in url else (b"\x00" * 512 + b"\xff" * 512))
                return True

            with patch("inside_case_factory.core.discovery._download", side_effect=download):
                result = discover_project_scene_media(project.root, connectors=[ShotConnector()], limit_per_source=2)
            assets = load_media_manifest(project.root)["assets"]
            self.assertEqual(result["uncovered_shots"], [])
            self.assertEqual(len(assets), 2)
            self.assertTrue(all(item["shot_ids"] == ["s01-shot-1"] for item in assets))
            self.assertTrue(all(item["shot_relevance_reason"] for item in assets))

    def test_research_script_scene_workflow_is_approval_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "A Factual Case")

            with self.assertRaises(RuntimeError):
                generate_script(project.root)

            source = add_source(
                project.root,
                title="Official Record",
                url="https://example.test/official-record",
                publisher="Example Court",
                publication_date="2020-01-02",
                source_type="official_record",
                reliability_notes="Primary source fixture for workflow tests.",
            )
            claim = add_claim(
                project.root,
                text="The official record states that the hearing occurred.",
                source_ids=[source["id"]],
                confidence="high",
                date="2020-01-02",
                people="Example Person",
                locations="Example City",
                events="Hearing",
            )

            self.assertFalse(approve_research(project.root))
            review_item(project.root, "sources.json", "sources", source["id"], "approved")
            review_item(project.root, "claims.json", "claims", claim["id"], "approved")
            self.assertTrue(approve_research(project.root))

            script = generate_script(project.root, target_duration_minutes=10)
            self.assertIn(claim["id"], script["generated_from"])
            self.assertIn("official record", script["narration"])
            save_script_edit(project.root, script["narration"] + "\n\nEdited closing line.")

            with self.assertRaises(RuntimeError):
                generate_scenes(project.root)

            self.assertTrue(approve_script(project.root))
            scenes = generate_scenes(project.root)

            self.assertTrue(scenes["scenes"])
            self.assertEqual(scenes["scenes"][0]["claim_ids"], [claim["id"]])

    def test_tavily_provider_without_api_key_is_safe_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            project = create_project(Path(tmp), "A Factual Case")
            provider = TavilyResearchProvider(api_key=None)
            result = provider.research(project.root, "A Factual Case")
            manifest = read_json(project.root / "manifests" / "sources.json")

            self.assertFalse(result["ok"])
            self.assertEqual(result["message"], "TAVILY_API_KEY is not set.")
            self.assertEqual(manifest["sources"], [])

    def test_dotenv_loads_key_without_overriding_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"TAVILY_API_KEY": "existing"}, clear=True):
            env_path = Path(tmp) / ".env"
            env_path.write_text("TAVILY_API_KEY=from-file\nOTHER_VALUE='quoted'\n", encoding="utf-8")

            loaded = load_dotenv(Path(tmp))

            self.assertEqual(loaded["OTHER_VALUE"], "quoted")
            self.assertEqual(os.environ["TAVILY_API_KEY"], "existing")
            self.assertEqual(os.environ["OTHER_VALUE"], "quoted")

    def test_research_ranking_prefers_authoritative_sources(self) -> None:
        ranked = rank_research_results(
            [
                {
                    "title": "The Final 10.5 Hours of Michael Jackson",
                    "url": "https://www.youtube.com/watch?v=example",
                    "score": 0.99,
                },
                {
                    "title": "Everything You Need To Know About Michael Jackson",
                    "url": "https://example-blog.test/story",
                    "score": 0.98,
                },
                {
                    "title": "Death of Michael Jackson",
                    "url": "https://en.wikipedia.org/wiki/Death_of_Michael_Jackson",
                    "score": 0.70,
                },
                {
                    "title": "People v. Conrad Murray court records",
                    "url": "https://www.courts.ca.gov/example",
                    "score": 0.60,
                },
                {
                    "title": "AP timeline of Michael Jackson death",
                    "url": "https://apnews.com/example",
                    "score": 0.65,
                },
            ]
        )

        self.assertEqual(ranked[0]["source_type"], "official_record")
        self.assertEqual(ranked[1]["source_type"], "news")
        self.assertEqual(ranked[2]["source_type"], "reference")
        self.assertEqual(ranked[-1]["source_type"], "video")

    def test_tavily_research_extracts_sources_without_creating_snippet_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "A Factual Case")
            provider = TavilyResearchProvider(api_key="test-key")

            with patch.object(
                provider,
                "search",
                return_value={
                    "ok": True,
                    "results": [
                        {
                            "title": "Official Timeline",
                            "url": "https://example.gov/official-timeline",
                            "raw_content": (
                                "The official record was released by the agency. "
                                "The agency reported that the hearing was held in Example City. "
                                "This short line is ignored."
                            ),
                        }
                    ],
                },
            ), patch.object(
                provider,
                "extract",
                return_value={
                    "ok": True,
                    "results": [{
                        "url": "https://example.gov/official-timeline",
                        "raw_content": "The official record was released by the agency. The agency reported that the hearing was held in Example City.",
                    }],
                    "usage": {"credits": 1},
                },
            ):
                result = provider.research(project.root, "A Factual Case")

            sources = read_json(project.root / "manifests" / "sources.json")["sources"]
            claims = read_json(project.root / "manifests" / "claims.json")["claims"]

            self.assertFalse(result["ok"])
            self.assertEqual(sources[0]["review_status"], "pending_review")
            self.assertEqual(sources[0]["source_type"], "official_record")
            self.assertEqual(sources[0]["extraction_status"], "success")
            self.assertFalse(claims)
            snapshots = read_json(project.root / "manifests" / "source_snapshots.json")["snapshots"]
            self.assertEqual(len(snapshots), 1)

    def test_dashboard_project_page_shows_discovery_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "defaults.toml").write_text(
                "[app]\nname='test'\n[paths]\nprojects_dir='projects'\n[pipeline]\n[review_gates]\n[video]\n",
                encoding="utf-8",
            )
            (root / "config" / "providers.toml").write_text("", encoding="utf-8")
            create_project(root / "projects", "Example Jane", "example-jane")
            app = DashboardApp(root)
            status_headers: dict[str, object] = {}

            def start_response(status: str, headers: list[tuple[str, str]]) -> None:
                status_headers["status"] = status
                status_headers["headers"] = headers

            body = b"".join(
                app(
                    {
                        "REQUEST_METHOD": "GET",
                        "PATH_INFO": "/projects/example-jane",
                        "QUERY_STRING": "",
                        "wsgi.input": BytesIO(b""),
                        "CONTENT_LENGTH": "0",
                    },
                    start_response,
                )
            ).decode("utf-8")

            self.assertEqual(status_headers["status"], "200 OK")
            self.assertIn("Voortgang", body)
            self.assertIn("Onderzoek", body)
            self.assertIn("Script", body)
            self.assertIn("Scènes", body)
            self.assertIn("Beelden", body)
            self.assertIn("Geavanceerde instellingen", body)
            self.assertNotIn("Discover Archival Media", body)

            advanced_body = b"".join(
                app(
                    {
                        "REQUEST_METHOD": "GET",
                        "PATH_INFO": "/projects/example-jane/advanced",
                        "QUERY_STRING": "",
                        "wsgi.input": BytesIO(b""),
                        "CONTENT_LENGTH": "0",
                    },
                    start_response,
                )
            ).decode("utf-8")

            self.assertIn("Archiefbeelden zoeken", advanced_body)
            self.assertIn("Wachtrij voor ontdekte media", advanced_body)
            self.assertIn("Research", advanced_body)
            self.assertIn("Run Automated Research", advanced_body)
            self.assertIn("Script", body)
            self.assertIn("Scenes", advanced_body)

    def test_prompt_production_offline_fallback_creates_plan_without_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "defaults.toml").write_text(
                "[app]\nname='test'\n[paths]\nprojects_dir='projects'\n[pipeline]\n[review_gates]\n[video]\n",
                encoding="utf-8",
            )
            (root / "config" / "providers.toml").write_text("[research]\nprovider='local_stub'\n", encoding="utf-8")
            settings = load_settings(root)

            result = start_production(
                settings,
                ProductionRequest(
                    prompt=(
                        "Create a 12-minute Inside the Case documentary about the death of Michael Jackson. "
                        "Focus on his final 24 hours, Conrad Murray, the emergency response, the investigation and the trial."
                    ),
                    target_duration_minutes=12,
                    language="English",
                    autonomy_mode="review",
                ),
            )

            project_root = root / "projects" / result["project_slug"]
            plan = read_json(project_root / "manifests" / "production_plan.json")
            activity = read_json(project_root / "manifests" / "production_activity.json")
            research = read_json(project_root / "manifests" / "research.json")

            self.assertEqual(result["topic"], "death of Michael Jackson")
            self.assertEqual(plan["target_duration_minutes"], 12)
            self.assertEqual(plan["autonomy_mode"], "review")
            self.assertIn("TAVILY_API_KEY is not set", research["message"])
            self.assertIn("Review Mode pause", activity["current_activity"])
            self.assertFalse((project_root / "exports" / "final_video.mp4").exists())
            self.assertFalse(any((project_root / "assets" / "audio").glob("*.wav")))

    def test_openai_reasoning_analyzes_request_with_mocked_response(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            captured["headers"] = getattr(request, "headers", {})
            captured["body"] = json.loads(getattr(request, "data").decode("utf-8"))
            return FakeResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "text": json.dumps(
                                        {
                                            "version": 1,
                                            "status": "ready",
                                            "exact_topic": "Death of Michael Jackson",
                                            "documentary_angle": "Final hours and accountability",
                                            "requested_focus": "Final 24 hours",
                                            "target_duration_minutes": 12,
                                            "video_language": "English",
                                            "people": ["Michael Jackson", "Conrad Murray"],
                                            "locations": ["Los Angeles"],
                                            "dates": ["2009-06-25"],
                                            "events": ["Emergency response"],
                                            "exclusions": ["lyrics"],
                                            "factual_questions": ["What happened in the final hours?"],
                                            "involved_countries": [{"country": "United States", "language": "English", "reason": "Location of events"}],
                                            "relevant_languages": ["English"],
                                            "source_priorities": [{"level": 1, "categories": ["official records"]}],
                                            "coverage_targets": [{"country": "United States", "minimum_percentage": 80}],
                                        }
                                    )
                                }
                            ]
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 80},
                }
            )

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            project = create_project(Path(tmp), "A Factual Case")
            provider = OpenAIReasoningProvider(
                ReasoningConfig(enabled=True, model="gpt-5.5", estimated_cost_per_call_usd=0.01)
            )
            with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                plan = provider.analyze_request(
                    project.root,
                    {
                        "prompt": "Create a documentary about the death of Michael Jackson",
                        "target_duration_minutes": 12,
                        "language": "English",
                    },
                )

            self.assertEqual(plan["exact_topic"], "Death of Michael Jackson")
            self.assertEqual((project.root / "manifests" / "research_plan.json").exists(), True)
            self.assertEqual(captured["body"]["model"], "gpt-5.5")
            self.assertEqual(captured["body"]["reasoning"]["effort"], "medium")
            self.assertNotIn("test-key", json.dumps(captured["body"]))
            usage = read_json(project.root / "manifests" / "reasoning_usage.json")
            self.assertEqual(usage["calls"][0]["operation"], "research_plan")
            self.assertNotIn("test-key", json.dumps(usage))

    def test_gpt_4_1_stage_omits_unsupported_reasoning_effort(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({
                    "output_text": json.dumps({
                        "version": 1, "status": "ready", "exact_topic": "Test",
                        "documentary_angle": "", "requested_focus": "Test",
                        "target_duration_minutes": 1, "video_language": "English",
                        "people": [], "locations": [], "dates": [], "events": [],
                        "exclusions": [], "factual_questions": [],
                        "involved_countries": [], "relevant_languages": [],
                        "source_priorities": [], "coverage_targets": []
                    }),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }).encode("utf-8")

        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            captured.update(json.loads(getattr(request, "data").decode("utf-8")))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            project = create_project(Path(tmp), "Test")
            provider = OpenAIReasoningProvider(ReasoningConfig(enabled=True, model="gpt-4.1-nano"), api_key="test-key")
            with patch("inside_case_factory.providers.reasoning.urlopen", side_effect=fake_urlopen):
                provider.analyze_request(project.root, {"prompt": "Test"})

        self.assertEqual(captured["model"], "gpt-4.1-nano")
        self.assertNotIn("reasoning", captured)

    def test_openai_reasoning_source_analysis_creates_supported_claims_without_real_api(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "output": [
                            {
                                "content": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "version": 1,
                                                "status": "ready",
                                                "source_analysis": [
                                                    {
                                                        "source_id": "official-record",
                                                        "relevant": True,
                                                        "usable": True,
                                                        "source_type": "official_record",
                                                        "source_quality": "high",
                                                        "summary": "Official record confirms the hearing.",
                                                        "evidence_excerpts": ["The hearing was held in Example City."],
                                                        "rejection_reason": "",
                                                    }
                                                ],
                                                "claims": [
                                                    {
                                                        "text": "The hearing was held in Example City.",
                                                        "evidence_classification": "verified_fact",
                                                        "canonical_key": "hearing-example-city",
                                                        "research_question_ids": [],
                                                        "source_ids": ["official-record"],
                                                        "evidence": [{
                                                            "source_id": "official-record",
                                                            "exact_excerpt": "The hearing was held in Example City.",
                                                            "start": 0,
                                                            "end": 43,
                                                            "searchable_text": "The hearing was held in Example City."
                                                        }],
                                                        "relevance_score": 0.94,
                                                        "confidence": "high",
                                                        "source_quality": "high",
                                                        "corroboration_status": "single_primary_source",
                                                        "people": ["Example Person"],
                                                        "locations": ["Example City"],
                                                        "dates": ["2020-01-02"],
                                                        "events": ["Hearing"],
                                                        "contradiction_notes": "",
                                                        "review_status": "pending_review",
                                                    }
                                                ],
                                                "dossier": {
                                                    "version": 1,
                                                    "status": "draft",
                                                    "summary": "A concise dossier.",
                                                    "key_facts": ["The hearing was held in Example City."],
                                                    "corroborated_claim_ids": [],
                                                    "single_source_claim_ids": ["c001"],
                                                    "weak_source_claim_ids": [],
                                                    "contradictions": [],
                                                    "source_quality_notes": [],
                                                    "primary_evidence": ["official-record"],
                                                    "secondary_evidence": [],
                                                    "tertiary_evidence": [],
                                                    "unresolved_questions": [],
                                                },
                                                "timeline": {
                                                    "version": 1,
                                                    "events": [
                                                        {
                                                            "date": "2020-01-02",
                                                            "summary": "The hearing was held in Example City.",
                                                            "claim_ids": ["c001"],
                                                            "source_ids": ["official-record"],
                                                        }
                                                    ],
                                                },
                                            }
                                        )
                                    }
                                ]
                            }
                        ],
                        "usage": {"input_tokens": 200, "output_tokens": 120},
                    }
                ).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            project = create_project(Path(tmp), "A Factual Case")
            provider = TavilyResearchProvider(api_key="tavily-test")
            with patch.object(
                provider,
                "search",
                return_value={
                    "ok": True,
                    "results": [
                        {
                            "title": "Official Record",
                            "url": "https://example.gov/official-record",
                            "raw_content": "Navigation Click here. The hearing was held in Example City.",
                        }
                    ],
                },
            ), patch.object(
                provider,
                "extract",
                return_value={
                    "ok": True,
                    "results": [{
                        "url": "https://example.gov/official-record",
                        "raw_content": "The official record contains evidence. The hearing was held in Example City.",
                    }],
                    "usage": {"credits": 1},
                },
            ), patch("inside_case_factory.providers.reasoning.urlopen", return_value=FakeResponse()):
                result = provider.research(
                    project.root,
                    "A Factual Case",
                    reasoning_provider=OpenAIReasoningProvider(
                        ReasoningConfig(enabled=True, model="gpt-5.5", estimated_cost_per_call_usd=0.01)
                    ),
                    research_plan={"exact_topic": "A Factual Case"},
                )

            claims = read_json(project.root / "manifests" / "claims.json")["claims"]
            self.assertTrue(result["ok"])
            self.assertEqual(result["claims_added"], 1)
            self.assertEqual(claims[0]["text"], "The hearing was held in Example City.")
            self.assertEqual(claims[0]["evidence_excerpts"], ["The hearing was held in Example City."])
            self.assertEqual(claims[0]["source_quality"], "high")
            self.assertTrue((project.root / "manifests" / "source_analysis.json").exists())
            self.assertTrue((project.root / "manifests" / "dossier.json").exists())

    def test_openai_reasoning_enabled_without_api_key_stops_before_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"TAVILY_API_KEY": "unused"}, clear=True):
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "defaults.toml").write_text(
                "[app]\nname='test'\n[paths]\nprojects_dir='projects'\n[pipeline]\n[review_gates]\n[video]\n",
                encoding="utf-8",
            )
            (root / "config" / "providers.toml").write_text(
                "[research]\n[research.tavily]\nmax_results=1\n[reasoning]\nprovider='openai'\nenabled=true\n",
                encoding="utf-8",
            )
            settings = load_settings(root)
            result = start_production(
                settings,
                ProductionRequest(
                    prompt="Create a documentary about Example Jane.",
                    target_duration_minutes=8,
                    language="English",
                    autonomy_mode="review",
                ),
            )

            project_root = root / "projects" / result["project_slug"]
            research = read_json(project_root / "manifests" / "research.json")
            sources = read_json(project_root / "manifests" / "sources.json")
            self.assertEqual(research["status"], "blocked")
            self.assertIn("OPENAI_API_KEY is not set", research["message"])
            self.assertEqual(sources["sources"], [])


if __name__ == "__main__":
    unittest.main()
