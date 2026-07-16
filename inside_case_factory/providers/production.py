from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from inside_case_factory.utils.files import read_json, write_json


class ProductionProviderError(RuntimeError):
    pass


class BudgetExceededError(ProductionProviderError):
    pass


@dataclass(frozen=True)
class ProductionRequest:
    kind: str
    task: str
    prompt: str
    project_root: Path
    output_path: Path | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductionResponse:
    provider: str
    model: str
    kind: str
    content: str = ""
    data: bytes = b""
    cost_usd: float = 0.0
    cached: bool = False
    attempts: int = 1


class Transport(Protocol):
    def request(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> tuple[bytes, dict[str, str]]: ...
    def get(self, url: str, headers: dict[str, str], timeout: int) -> tuple[bytes, dict[str, str]]: ...


class UrlLibTransport:
    def request(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> tuple[bytes, dict[str, str]]:
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json", **headers})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers.items())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ProductionProviderError(f"HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise ProductionProviderError(f"Network error: {error.reason}") from error

    def get(self, url: str, headers: dict[str, str], timeout: int) -> tuple[bytes, dict[str, str]]:
        request = Request(url, method="GET", headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers.items())
        except (HTTPError, URLError) as error:
            raise ProductionProviderError(f"GET failed: {error}") from error


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str
    model: str
    enabled: bool = False
    priority: int = 50
    quality: float = 0.8
    estimated_cost_usd: float = 0.0
    api_key_env: str = ""
    endpoint: str = ""
    timeout_seconds: int = 90
    options: dict[str, Any] = field(default_factory=dict)


class ProductionProvider:
    def __init__(self, config: ProviderConfig, transport: Transport | None = None) -> None:
        self.config = config
        self.transport = transport or UrlLibTransport()

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def available(self) -> bool:
        key_available = not self.config.api_key_env or bool(os.environ.get(self.config.api_key_env))
        return self.config.enabled and key_available

    def headers(self) -> dict[str, str]:
        return {}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        raise NotImplementedError


class OpenAITextProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ.get(self.config.api_key_env, '')}"}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(
            self.config.endpoint or "https://api.openai.com/v1/responses", self.headers(),
            {"model": self.config.model, "input": request.prompt, "store": False, **request.options}, self.config.timeout_seconds,
        )
        payload = json.loads(body)
        text = str(payload.get("output_text", ""))
        if not text:
            text = "".join(
                str(part.get("text", "")) for item in payload.get("output", []) for part in item.get("content", [])
                if isinstance(part, dict)
            )
        if not text:
            raise ProductionProviderError("OpenAI returned no text output.")
        return ProductionResponse(self.name, self.config.model, "text", content=text, cost_usd=self.config.estimated_cost_usd)


class GeminiTextProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": os.environ.get(self.config.api_key_env, "")}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        endpoint = self.config.endpoint or f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.model}:generateContent"
        body, _ = self.transport.request(endpoint, self.headers(), {"contents": [{"role": "user", "parts": [{"text": request.prompt}]}], **request.options}, self.config.timeout_seconds)
        payload = json.loads(body)
        text = "".join(str(part.get("text", "")) for candidate in payload.get("candidates", []) for part in candidate.get("content", {}).get("parts", []))
        if not text:
            raise ProductionProviderError("Gemini returned no text output.")
        return ProductionResponse(self.name, self.config.model, "text", content=text, cost_usd=self.config.estimated_cost_usd)


class ClaudeTextProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"x-api-key": os.environ.get(self.config.api_key_env, ""), "anthropic-version": "2023-06-01"}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(
            self.config.endpoint or "https://api.anthropic.com/v1/messages", self.headers(),
            {"model": self.config.model, "max_tokens": int(request.options.get("max_tokens", 4096)), "messages": [{"role": "user", "content": request.prompt}]}, self.config.timeout_seconds,
        )
        payload = json.loads(body)
        text = "".join(str(item.get("text", "")) for item in payload.get("content", []) if item.get("type") == "text")
        if not text:
            raise ProductionProviderError("Claude returned no text output.")
        return ProductionResponse(self.name, self.config.model, "text", content=text, cost_usd=self.config.estimated_cost_usd)


class LocalTextProvider(ProductionProvider):
    @property
    def available(self) -> bool:
        return self.config.enabled

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(
            self.config.endpoint or "http://127.0.0.1:11434/v1/chat/completions", {},
            {"model": self.config.model, "messages": [{"role": "user", "content": request.prompt}], "stream": False}, self.config.timeout_seconds,
        )
        payload = json.loads(body)
        text = str(payload.get("choices", [{}])[0].get("message", {}).get("content", ""))
        if not text:
            raise ProductionProviderError("Local model returned no text output.")
        return ProductionResponse(self.name, self.config.model, "text", content=text)


class OpenAITTSProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ.get(self.config.api_key_env, '')}"}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(
            self.config.endpoint or "https://api.openai.com/v1/audio/speech", self.headers(),
            {"model": self.config.model, "input": request.prompt, "voice": request.options.get("voice", self.config.options.get("voice", "marin")),
             "instructions": request.options.get("instructions", "Natural, restrained documentary narration."), "response_format": "wav"}, self.config.timeout_seconds,
        )
        return ProductionResponse(self.name, self.config.model, "voice", data=body, cost_usd=self.config.estimated_cost_usd)


class ElevenLabsProductionProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"xi-api-key": os.environ.get(self.config.api_key_env, ""), "Accept": "audio/mpeg"}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        voice = str(request.options.get("voice_id", self.config.options.get("voice_id", "JBFqnCBsd6RMkjVDRZzb")))
        endpoint = self.config.endpoint or f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
        body, _ = self.transport.request(endpoint, self.headers(), {"text": request.prompt, "model_id": self.config.model, "voice_settings": self.config.options.get("voice_settings", {})}, self.config.timeout_seconds)
        return ProductionResponse(self.name, self.config.model, "voice", data=body, cost_usd=self.config.estimated_cost_usd)


class OpenAIImageProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ.get(self.config.api_key_env, '')}"}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(self.config.endpoint or "https://api.openai.com/v1/images/generations", self.headers(), {"model": self.config.model, "prompt": request.prompt, "size": request.options.get("size", "1536x1024"), "response_format": "b64_json"}, self.config.timeout_seconds)
        payload = json.loads(body)
        data = base64.b64decode(payload.get("data", [{}])[0].get("b64_json", ""))
        if not data:
            raise ProductionProviderError("OpenAI Images returned no image.")
        return ProductionResponse(self.name, self.config.model, "image", data=data, cost_usd=self.config.estimated_cost_usd)


class GeminiImageProvider(GeminiTextProvider):
    def generate(self, request: ProductionRequest) -> ProductionResponse:
        endpoint = self.config.endpoint or f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.model}:generateContent"
        body, _ = self.transport.request(endpoint, self.headers(), {"contents": [{"parts": [{"text": request.prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}, self.config.timeout_seconds)
        payload = json.loads(body)
        parts = [part for candidate in payload.get("candidates", []) for part in candidate.get("content", {}).get("parts", [])]
        encoded = next((part.get("inlineData", {}).get("data") or part.get("inline_data", {}).get("data") for part in parts if part.get("inlineData") or part.get("inline_data")), "")
        if not encoded:
            raise ProductionProviderError("Gemini returned no image.")
        return ProductionResponse(self.name, self.config.model, "image", data=base64.b64decode(encoded), cost_usd=self.config.estimated_cost_usd)


class FluxImageProvider(ProductionProvider):
    def headers(self) -> dict[str, str]:
        return {"x-key": os.environ.get(self.config.api_key_env, "")}

    def generate(self, request: ProductionRequest) -> ProductionResponse:
        body, _ = self.transport.request(self.config.endpoint or f"https://api.bfl.ai/v1/{self.config.model}", self.headers(), {"prompt": request.prompt, **request.options}, self.config.timeout_seconds)
        payload = json.loads(body)
        polling_url = str(payload.get("polling_url") or payload.get("pollingUrl") or "")
        if not polling_url:
            raise ProductionProviderError("Flux returned no polling URL.")
        if not hasattr(self.transport, "get"):
            raise ProductionProviderError("Flux transport does not support asynchronous polling.")
        for poll in range(int(request.options.get("max_polls", 60))):
            status_body, _ = self.transport.get(polling_url, self.headers(), self.config.timeout_seconds)  # type: ignore[attr-defined]
            status = json.loads(status_body)
            state = str(status.get("status", "")).lower()
            if state in {"ready", "completed", "succeeded"}:
                image_url = str(status.get("result", {}).get("sample") or status.get("result", {}).get("url") or "")
                if not image_url:
                    raise ProductionProviderError("Flux completed without an image URL.")
                image, _ = self.transport.get(image_url, {}, self.config.timeout_seconds)  # type: ignore[attr-defined]
                return ProductionResponse(self.name, self.config.model, "image", data=image, cost_usd=self.config.estimated_cost_usd)
            if state in {"failed", "error"}:
                raise ProductionProviderError(f"Flux generation failed: {status}")
            if poll < int(request.options.get("max_polls", 60)) - 1:
                time.sleep(float(request.options.get("poll_interval_seconds", 1.0)))
        raise ProductionProviderError("Flux generation polling timed out.")


PROVIDER_CLASSES: dict[str, type[ProductionProvider]] = {
    "openai_text": OpenAITextProvider, "gemini_text": GeminiTextProvider, "claude_text": ClaudeTextProvider,
    "local_text": LocalTextProvider, "openai_tts": OpenAITTSProvider, "elevenlabs": ElevenLabsProductionProvider,
    "openai_images": OpenAIImageProvider, "gemini_images": GeminiImageProvider, "flux": FluxImageProvider,
}


class ProductionProviderRouter:
    def __init__(
        self, project_root: Path, providers: list[ProductionProvider], *, budget_usd: float = 0.0,
        retries: int = 2, cache_enabled: bool = True, sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.project_root = project_root
        self.providers = providers
        self.budget_usd = max(0.0, budget_usd)
        self.retries = max(0, retries)
        self.cache_enabled = cache_enabled
        self.sleeper = sleeper
        self.cache_dir = project_root / "workspace" / "provider_cache"
        self.ledger_path = project_root / "manifests" / "provider_usage.json"

    @classmethod
    def from_settings(cls, project_root: Path, settings: dict[str, Any], transport: Transport | None = None) -> "ProductionProviderRouter":
        production = dict(settings.get("production", {}))
        override_path = project_root / "manifests" / "provider_config.json"
        if override_path.exists():
            override = read_json(override_path)
            production = {**production, **override, "providers": {**production.get("providers", {}), **override.get("providers", {})}}
        external_calls_enabled = bool(production.get("external_calls_enabled", False))
        configs = []
        for name, raw in production.get("providers", {}).items():
            if not isinstance(raw, dict) or name not in PROVIDER_CLASSES:
                continue
            configs.append(ProviderConfig(
                name=name, kind=str(raw.get("kind", name.split("_")[-1])), model=str(raw.get("model", "")),
                enabled=bool(raw.get("enabled", False)) and (external_calls_enabled or name == "local_text"), priority=int(raw.get("priority", 50)), quality=float(raw.get("quality", .8)),
                estimated_cost_usd=float(raw.get("estimated_cost_usd", 0)), api_key_env=str(raw.get("api_key_env", "")),
                endpoint=str(raw.get("endpoint", "")), timeout_seconds=int(raw.get("timeout_seconds", 90)), options=dict(raw.get("options", {})),
            ))
        providers = [PROVIDER_CLASSES[config.name](config, transport) for config in configs]
        return cls(project_root, providers, budget_usd=float(production.get("budget_usd", 0)), retries=int(production.get("retries", 2)), cache_enabled=bool(production.get("cache_enabled", True)))

    def _ledger(self) -> dict[str, Any]:
        return read_json(self.ledger_path) if self.ledger_path.exists() else {"version": 1, "spent_usd": 0.0, "calls": []}

    def candidates(self, kind: str, task: str) -> list[ProductionProvider]:
        task_preferences = _optional_provider_config(self.project_root).get("tasks", {}).get(task, [])
        preference = {str(name): index for index, name in enumerate(task_preferences)}
        available = [provider for provider in self.providers if provider.config.kind == kind and provider.available]
        return sorted(available, key=lambda provider: (
            preference.get(provider.name, 999), provider.config.priority,
            -provider.config.quality, provider.config.estimated_cost_usd,
        ))

    def choose(self, kind: str, task: str) -> ProductionProvider | None:
        candidates = self.candidates(kind, task)
        return candidates[0] if candidates else None

    def execute(self, request: ProductionRequest) -> ProductionResponse:
        cache_key = hashlib.sha256(json.dumps({"kind": request.kind, "task": request.task, "prompt": request.prompt, "options": request.options}, sort_keys=True).encode()).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        if self.cache_enabled and cache_path.exists():
            cached = read_json(cache_path)
            data = base64.b64decode(cached.get("data", "")) if cached.get("data") else b""
            return ProductionResponse(cached["provider"], cached["model"], cached["kind"], cached.get("content", ""), data, float(cached.get("cost_usd", 0)), True, 0)
        errors = []
        for provider in self.candidates(request.kind, request.task):
            ledger = self._ledger()
            projected = float(ledger.get("spent_usd", 0)) + provider.config.estimated_cost_usd
            if self.budget_usd and projected > self.budget_usd:
                errors.append(f"{provider.name}: budget exceeded")
                continue
            for attempt in range(1, self.retries + 2):
                try:
                    response = provider.generate(request)
                    response = ProductionResponse(response.provider, response.model, response.kind, response.content, response.data, response.cost_usd, False, attempt)
                    ledger["spent_usd"] = round(float(ledger.get("spent_usd", 0)) + response.cost_usd, 6)
                    ledger.setdefault("calls", []).append({"provider": provider.name, "task": request.task, "kind": request.kind, "cost_usd": response.cost_usd, "attempts": attempt})
                    write_json(self.ledger_path, ledger)
                    if request.output_path and response.data:
                        request.output_path.parent.mkdir(parents=True, exist_ok=True)
                        request.output_path.write_bytes(response.data)
                    if self.cache_enabled:
                        write_json(cache_path, {"provider": response.provider, "model": response.model, "kind": response.kind, "content": response.content, "data": base64.b64encode(response.data).decode(), "cost_usd": response.cost_usd})
                    return response
                except ProductionProviderError as error:
                    errors.append(f"{provider.name} attempt {attempt}: {error}")
                    if attempt <= self.retries:
                        self.sleeper(min(4.0, .25 * (2 ** (attempt - 1))))
        if any("budget exceeded" in error for error in errors) and not any("attempt" in error for error in errors):
            raise BudgetExceededError("; ".join(errors))
        raise ProductionProviderError("All provider fallbacks failed: " + "; ".join(errors or ["no provider available"]))

    def selection_manifest(self, tasks: list[tuple[str, str]]) -> dict[str, Any]:
        selections = {}
        for task, kind in tasks:
            provider = self.choose(kind, task)
            selections[task] = {"kind": kind, "provider": provider.name if provider else "local_deterministic", "model": provider.config.model if provider else "built_in"}
        payload = {"version": 1, "selections": selections}
        write_json(self.project_root / "manifests" / "provider_selection.json", payload)
        return payload


def _optional_provider_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "manifests" / "provider_config.json"
    return read_json(path) if path.exists() else {}
