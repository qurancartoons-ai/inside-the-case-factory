import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inside_case_factory.core.autonomous_direction import CriticEngine, DirectorEngine
from inside_case_factory.core.producer import ProducerEngine
from inside_case_factory.core.project import create_project
from inside_case_factory.providers.production import (
    BudgetExceededError, ClaudeTextProvider, GeminiImageProvider, GeminiTextProvider,
    FluxImageProvider, LocalTextProvider, OpenAIImageProvider, OpenAITTSProvider, OpenAITextProvider,
    ProductionProvider, ProductionProviderError, ProductionProviderRouter, ProductionRequest,
    ProductionResponse, ProviderConfig,
)
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.web.dashboard import DashboardApp
from inside_case_factory.providers.reasoning import StructuredTextReasoningProvider, reasoning_provider_from_settings


class FakeTransport:
    def __init__(self, responses: list[bytes] | None = None, failures: int = 0) -> None:
        self.responses = list(responses or [])
        self.failures = failures
        self.calls = []

    def request(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        if self.failures:
            self.failures -= 1
            raise ProductionProviderError("temporary")
        return self.responses.pop(0), {}


class FluxTransport(FakeTransport):
    def __init__(self):
        super().__init__([b'{"polling_url":"https://poll"}'])
        self.get_calls = []

    def get(self, url, headers, timeout):
        self.get_calls.append(url)
        if url == "https://poll":
            return b'{"status":"Ready","result":{"sample":"https://image"}}', {}
        return b"flux-image", {}


class FakeProvider(ProductionProvider):
    def __init__(self, config, outputs=None, failures=0):
        super().__init__(config, FakeTransport())
        self.outputs = list(outputs or ["ok"])
        self.failures = failures
        self.call_count = 0

    @property
    def available(self):
        return self.config.enabled

    def generate(self, request):
        self.call_count += 1
        if self.failures:
            self.failures -= 1
            raise ProductionProviderError("temporary")
        value = self.outputs.pop(0)
        return ProductionResponse(self.name, self.config.model, request.kind, content=value, data=b"asset" if request.kind != "text" else b"", cost_usd=self.config.estimated_cost_usd)


def config(name, kind="text", priority=10, cost=.01, quality=.8):
    return ProviderConfig(name, kind, "model", enabled=True, priority=priority, quality=quality, estimated_cost_usd=cost)


class ProductionProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = create_project(Path(self.temporary.name), "Provider Case").root

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, kind="text", task="script", output=None):
        return ProductionRequest(kind, task, "prompt", self.root, output_path=output)

    def test_openai_gemini_claude_and_local_text_adapters_parse_official_shapes(self):
        adapters = [
            (OpenAITextProvider, b'{"output_text":"openai"}', "openai"),
            (GeminiTextProvider, b'{"candidates":[{"content":{"parts":[{"text":"gemini"}]}}]}', "gemini"),
            (ClaudeTextProvider, b'{"content":[{"type":"text","text":"claude"}]}', "claude"),
            (LocalTextProvider, b'{"choices":[{"message":{"content":"local"}}]}', "local"),
        ]
        with patch.dict(os.environ, {"KEY": "secret"}):
            for provider_class, response, expected in adapters:
                provider = provider_class(ProviderConfig(expected, "text", "model", True, api_key_env="KEY"), FakeTransport([response]))
                self.assertEqual(provider.generate(self.request()).content, expected)

    def test_text_adapters_build_provider_specific_requests(self):
        with patch.dict(os.environ, {"KEY": "secret"}):
            transports = [FakeTransport([b'{"output_text":"ok"}']), FakeTransport([b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}']), FakeTransport([b'{"content":[{"type":"text","text":"ok"}]}'])]
            providers = [
                OpenAITextProvider(ProviderConfig("openai", "text", "gpt", True, api_key_env="KEY"), transports[0]),
                GeminiTextProvider(ProviderConfig("gemini", "text", "gemini", True, api_key_env="KEY"), transports[1]),
                ClaudeTextProvider(ProviderConfig("claude", "text", "claude", True, api_key_env="KEY"), transports[2]),
            ]
            for provider in providers:
                provider.generate(self.request())
            self.assertEqual(transports[0].calls[0][2]["input"], "prompt")
            self.assertEqual(transports[1].calls[0][2]["contents"][0]["parts"][0]["text"], "prompt")
            self.assertEqual(transports[2].calls[0][2]["messages"][0]["content"], "prompt")

    def test_openai_tts_returns_binary_audio(self):
        with patch.dict(os.environ, {"KEY": "secret"}):
            transport = FakeTransport([b"wave"])
            provider = OpenAITTSProvider(ProviderConfig("openai_tts", "voice", "gpt-4o-mini-tts", True, api_key_env="KEY"), transport)
            response = provider.generate(self.request("voice", "voice_over"))
            self.assertEqual(response.data, b"wave")
            self.assertEqual(transport.calls[0][2]["response_format"], "wav")

    def test_openai_and_gemini_image_adapters_decode_base64(self):
        encoded = base64.b64encode(b"png").decode()
        with patch.dict(os.environ, {"KEY": "secret"}):
            openai = OpenAIImageProvider(ProviderConfig("openai_images", "image", "gpt-image", True, api_key_env="KEY"), FakeTransport([json.dumps({"data": [{"b64_json": encoded}]}).encode()]))
            gemini = GeminiImageProvider(ProviderConfig("gemini_images", "image", "gemini-image", True, api_key_env="KEY"), FakeTransport([json.dumps({"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]}).encode()]))
            self.assertEqual(openai.generate(self.request("image", "scene_image")).data, b"png")
            self.assertEqual(gemini.generate(self.request("image", "scene_image")).data, b"png")

    def test_flux_adapter_completes_asynchronous_polling(self):
        with patch.dict(os.environ, {"BFL_API_KEY": "secret"}):
            transport = FluxTransport()
            provider = FluxImageProvider(ProviderConfig("flux", "image", "flux-2-pro", True, api_key_env="BFL_API_KEY"), transport)
            response = provider.generate(ProductionRequest("image", "scene_image", "prompt", self.root, options={"max_polls": 1}))
        self.assertEqual(response.data, b"flux-image")
        self.assertEqual(transport.get_calls, ["https://poll", "https://image"])

    def test_router_honors_project_task_preference_over_global_priority(self):
        first = FakeProvider(config("first", priority=1))
        preferred = FakeProvider(config("preferred", priority=50))
        write_json(self.root / "manifests/provider_config.json", {"tasks": {"script": ["preferred", "first"]}})
        router = ProductionProviderRouter(self.root, [first, preferred], budget_usd=1)
        self.assertEqual(router.choose("text", "script").name, "preferred")

    def test_router_retries_then_succeeds(self):
        provider = FakeProvider(config("retry"), failures=2)
        router = ProductionProviderRouter(self.root, [provider], budget_usd=1, retries=2, sleeper=lambda _: None)
        response = router.execute(self.request())
        self.assertEqual(response.attempts, 3)
        self.assertEqual(provider.call_count, 3)

    def test_router_falls_back_after_provider_failure(self):
        broken = FakeProvider(config("broken", priority=1), failures=5)
        fallback = FakeProvider(config("fallback", priority=2))
        router = ProductionProviderRouter(self.root, [broken, fallback], budget_usd=1, retries=1, sleeper=lambda _: None)
        self.assertEqual(router.execute(self.request()).provider, "fallback")

    def test_budget_blocks_call_before_external_side_effect(self):
        provider = FakeProvider(config("costly", cost=.5))
        router = ProductionProviderRouter(self.root, [provider], budget_usd=.1)
        with self.assertRaises(BudgetExceededError):
            router.execute(self.request())
        self.assertEqual(provider.call_count, 0)

    def test_cache_avoids_second_call_and_cost(self):
        provider = FakeProvider(config("cached", cost=.05))
        router = ProductionProviderRouter(self.root, [provider], budget_usd=1)
        first = router.execute(self.request())
        second = router.execute(self.request())
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(read_json(self.root / "manifests/provider_usage.json")["spent_usd"], .05)

    def test_binary_result_is_written_to_requested_path(self):
        provider = FakeProvider(config("image", "image"))
        router = ProductionProviderRouter(self.root, [provider], budget_usd=1)
        output = self.root / "assets/generated/image.png"
        router.execute(self.request("image", "scene_image", output))
        self.assertEqual(output.read_bytes(), b"asset")

    def test_settings_require_explicit_project_enable_for_external_calls(self):
        settings = {"production": {"external_calls_enabled": False, "providers": {
            "openai_text": {"kind": "text", "enabled": True, "model": "gpt", "api_key_env": "OPENAI_API_KEY"},
            "local_text": {"kind": "text", "enabled": True, "model": "local"},
        }}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}):
            router = ProductionProviderRouter.from_settings(self.root, settings, FakeTransport())
        self.assertEqual(router.choose("text", "script").name, "local_text")
        write_json(self.root / "manifests/provider_config.json", {"external_calls_enabled": True})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}):
            enabled = ProductionProviderRouter.from_settings(self.root, settings, FakeTransport())
            self.assertEqual(enabled.choose("text", "script").name, "openai_text")

    def test_producer_director_and_critic_record_automatic_provider_choice(self):
        provider = FakeProvider(config("best"))
        router = ProductionProviderRouter(self.root, [provider], budget_usd=1)
        scenes = [{"id": f"s{i}", "duration_seconds": 10, "narration": "text", "claim_ids": [], "dates": [], "events": []} for i in range(1, 5)]
        producer = ProducerEngine().plan(self.root, scenes, provider_router=router)
        director = DirectorEngine().plan(self.root, scenes, width=1920, height=1080, provider_router=router)
        (self.root / "manifests/subtitles.srt").write_text("x", encoding="utf-8")
        critic = CriticEngine().analyze(self.root, render_number=1, duration_seconds=40, provider_router=router)
        self.assertEqual(producer["provider_selection"]["provider"], "best")
        self.assertEqual(director["director"]["provider_selection"]["provider"], "best")
        self.assertEqual(critic["provider_selection"]["provider"], "best")

    def test_selection_manifest_covers_text_voice_and_image_tasks(self):
        providers = [FakeProvider(config("text")), FakeProvider(config("voice", "voice")), FakeProvider(config("image", "image"))]
        router = ProductionProviderRouter(self.root, providers, budget_usd=1)
        manifest = router.selection_manifest([("producer_blueprint", "text"), ("voice_over", "voice"), ("scene_image", "image")])
        self.assertEqual(manifest["selections"]["voice_over"]["provider"], "voice")
        self.assertEqual(manifest["selections"]["scene_image"]["provider"], "image")

    def test_dashboard_exposes_per_project_provider_configuration(self):
        html = DashboardApp(Path.cwd()).production_provider_panel(self.root, "provider-case")
        for label in ("Projectbudget USD", "Producer", "Director", "Critic", "Voice-over", "Scènebeelden"):
            self.assertIn(label, html)

    def test_reasoning_factory_supports_gemini_claude_and_local_models(self):
        for name in ("gemini", "claude", "local"):
            provider = reasoning_provider_from_settings({"provider": name, "enabled": True, "model": "model", "dry_run": False})
            self.assertIsInstance(provider, StructuredTextReasoningProvider)
            self.assertEqual(provider.name, name)


if __name__ == "__main__":
    unittest.main()
