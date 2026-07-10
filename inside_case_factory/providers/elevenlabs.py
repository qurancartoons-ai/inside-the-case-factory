from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_MODEL_ID = "eleven_v3"
DEFAULT_STABILITY = 0.55


class ElevenLabsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElevenLabsConfig:
    enabled: bool = False
    voice_id: str = DEFAULT_VOICE_ID
    model_id: str = DEFAULT_MODEL_ID
    stability: float = DEFAULT_STABILITY
    similarity_boost: float = 0.8
    style: float = 0.0
    use_speaker_boost: bool = True
    output_format: str = "mp3_44100_128"


class ElevenLabsVoiceOverProvider:
    name = "elevenlabs"
    base_url = "https://api.elevenlabs.io"

    def __init__(self, config: ElevenLabsConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self.api_key)

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        if not self.available:
            raise ElevenLabsError("ElevenLabs is not enabled or ELEVENLABS_API_KEY is missing.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        temp_mp3 = output_path.with_suffix(".elevenlabs.mp3")

        query = urlencode({"output_format": self.config.output_format})
        url = f"{self.base_url}/v1/text-to-speech/{self.config.voice_id}?{query}"
        payload = {
            "text": text,
            "model_id": self.config.model_id,
            "voice_settings": {
                "stability": self.config.stability,
                "similarity_boost": self.config.similarity_boost,
                "style": self.config.style,
                "use_speaker_boost": self.config.use_speaker_boost,
            },
        }
        response = self._request("POST", url, payload)
        temp_mp3.write_bytes(response)

        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(temp_mp3),
                "-ar",
                "44100",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
        )
        temp_mp3.unlink(missing_ok=True)
        return output_path

    def list_voices(self, page_size: int = 20) -> dict[str, Any]:
        if not self.api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY is missing.")
        query = urlencode({"page_size": page_size})
        payload = self._request("GET", f"{self.base_url}/v2/voices?{query}")
        return json.loads(payload.decode("utf-8"))

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> bytes:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "xi-api-key": self.api_key or "",
                "Content-Type": "application/json",
                "Accept": "application/json" if method == "GET" else "audio/mpeg",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ElevenLabsError(f"ElevenLabs API error {error.code}: {detail}") from error
        except URLError as error:
            raise ElevenLabsError(f"ElevenLabs network error: {error}") from error


def elevenlabs_config_from_settings(settings: dict[str, Any]) -> ElevenLabsConfig:
    return ElevenLabsConfig(
        enabled=bool(settings.get("enabled", False)),
        voice_id=str(settings.get("voice_id", DEFAULT_VOICE_ID)),
        model_id=str(settings.get("model_id", DEFAULT_MODEL_ID)),
        stability=float(settings.get("stability", DEFAULT_STABILITY)),
        similarity_boost=float(settings.get("similarity_boost", 0.8)),
        style=float(settings.get("style", 0.0)),
        use_speaker_boost=bool(settings.get("use_speaker_boost", True)),
        output_format=str(settings.get("output_format", "mp3_44100_128")),
    )
