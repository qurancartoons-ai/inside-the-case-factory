from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from inside_case_factory.providers.production import ProductionProviderRouter, ProductionRequest


class RoutedVoiceOverProvider:
    name = "production_router"

    def __init__(self, router: ProductionProviderRouter, project_root: Path) -> None:
        self.router = router
        self.project_root = project_root

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        response = self.router.execute(ProductionRequest("voice", "voice_over", text, self.project_root))
        temporary = output_path.with_suffix(".provider-audio")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(response.data)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(temporary), "-ar", "44100", "-ac", "1", str(output_path)], check=True)
        temporary.unlink(missing_ok=True)
        return output_path


class FailoverVoiceOverProvider:
    name = "production_router_with_local_fallback"

    def __init__(self, primary: object, fallback: object) -> None:
        self.primary = primary
        self.fallback = fallback

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        try:
            return self.primary.synthesize_to_file(text, output_path, text_path)  # type: ignore[attr-defined]
        except Exception:
            output_path.unlink(missing_ok=True)
            return self.fallback.synthesize_to_file(text, output_path, text_path)  # type: ignore[attr-defined]


class RoutedImageProvider:
    name = "production_router"

    def __init__(self, router: ProductionProviderRouter, project_root: Path) -> None:
        self.router = router
        self.project_root = project_root

    def generate_to_file(self, prompt: str, output_path: Path) -> Path:
        response = self.router.execute(ProductionRequest("image", "scene_image", prompt, self.project_root, output_path=output_path))
        if not response.data:
            raise RuntimeError(f"{response.provider} did not return completed image bytes.")
        return output_path


@dataclass(frozen=True)
class RuntimeMediaProviderStatus:
    configured: bool
    key_available: bool
    attempted: bool
    candidates_returned: int
    skipped_reason: str = ""
    error_reason: str = ""


class RuntimeMediaTransport(Protocol):
    def get_json(self, url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]: ...


class UrlLibRuntimeMediaTransport:
    def get_json(self, url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except TimeoutError as error:
            raise RuntimeError("request timed out") from error
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"network error: {error.reason}") from error


class PexelsStockMediaProvider:
    """Native stock-media adapter for Pexels.

    This provider returns normalized candidate records that the existing discovery
    pipeline can score, deduplicate, and gate before selection.
    """

    name = "pexels"

    def __init__(
        self,
        *,
        enabled: bool = False,
        api_key_env: str = "PEXELS_API_KEY",
        timeout_seconds: int = 20,
        per_query_limit: int = 6,
        transport: RuntimeMediaTransport | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_key_env = str(api_key_env or "PEXELS_API_KEY")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.per_query_limit = max(1, min(80, int(per_query_limit)))
        self.transport = transport or UrlLibRuntimeMediaTransport()
        self.last_status = RuntimeMediaProviderStatus(
            configured=self.enabled,
            key_available=bool(os.environ.get(self.api_key_env)),
            attempted=False,
            candidates_returned=0,
            skipped_reason="provider disabled by configuration" if not self.enabled else (f"{self.api_key_env} is not set" if not os.environ.get(self.api_key_env) else ""),
        )

    def availability(self) -> dict[str, Any]:
        key_available = bool(os.environ.get(self.api_key_env))
        skipped = ""
        if not self.enabled:
            skipped = "provider disabled by configuration"
        elif not key_available:
            skipped = f"{self.api_key_env} is not set"
        return {
            "configured": self.enabled,
            "key_available": key_available,
            "attempted": False,
            "candidates_returned": 0,
            "skipped_reason": skipped,
            "error_reason": "",
        }

    def _image_endpoint(self, query: str, limit: int) -> str:
        return "https://api.pexels.com/v1/search?" + urlencode(
            {
                "query": query,
                "per_page": str(max(1, min(80, limit))),
                "orientation": "landscape",
            }
        )

    def _video_endpoint(self, query: str, limit: int) -> str:
        return "https://api.pexels.com/videos/search?" + urlencode(
            {
                "query": query,
                "per_page": str(max(1, min(80, limit))),
                "orientation": "landscape",
            }
        )

    @staticmethod
    def _safe_text(value: Any, fallback: str = "") -> str:
        text = str(value or "").strip()
        return text if text else fallback

    def _normalize_image(self, item: dict[str, Any], *, scene_id: str, shot_id: str) -> dict[str, Any] | None:
        source_id = str(item.get("id") or "").strip()
        src = item.get("src", {}) if isinstance(item.get("src"), dict) else {}
        preview_url = self._safe_text(src.get("large2x") or src.get("large") or src.get("medium") or src.get("original"))
        source_url = self._safe_text(item.get("url") or (f"https://www.pexels.com/photo/{source_id}/" if source_id else ""))
        if not source_id or not preview_url or not source_url:
            return None
        title = self._safe_text(item.get("alt"), f"Pexels image {source_id}")
        photographer = self._safe_text(item.get("photographer"))
        photographer_url = self._safe_text(item.get("photographer_url"))
        return {
            "provider": self.name,
            "source": self.name,
            "source_id": source_id,
            "source_url": source_url,
            "preview_url": preview_url,
            "download_url": self._safe_text(src.get("original"), preview_url),
            "media_type": "image",
            "title": title,
            "description": title,
            "creator": photographer,
            "date": "",
            "license": "Pexels License",
            "attribution_requirements": photographer,
            "usage_notes": "Stock media from Pexels. Verify suitability for documentary context.",
            "copyright_status": "licensed",
            "source_type": "stock",
            "source_category": "generic_stock_footage",
            "dimensions": {"width": int(item.get("width") or 0), "height": int(item.get("height") or 0)},
            "duration_seconds": None,
            "attribution": {
                "author": photographer,
                "author_url": photographer_url,
            },
            "license_metadata": {
                "name": "Pexels License",
                "url": "https://www.pexels.com/license/",
            },
            "scene_linkage": {"scene_id": scene_id, "shot_id": shot_id},
            "provider_metadata": {
                "pexels_id": source_id,
                "photographer_url": photographer_url,
                "avg_color": self._safe_text(item.get("avg_color")),
            },
        }

    def _normalize_video(self, item: dict[str, Any], *, scene_id: str, shot_id: str) -> dict[str, Any] | None:
        source_id = str(item.get("id") or "").strip()
        source_url = self._safe_text(item.get("url") or (f"https://www.pexels.com/video/{source_id}/" if source_id else ""))
        preview_url = self._safe_text(item.get("image"))
        files = [entry for entry in item.get("video_files", []) if isinstance(entry, dict)]
        mp4_files = [entry for entry in files if "mp4" in str(entry.get("file_type", "")).lower()]
        mp4_files.sort(key=lambda entry: int(entry.get("width") or 0), reverse=True)
        selected = next((entry for entry in mp4_files if int(entry.get("width") or 0) <= 1280), mp4_files[0] if mp4_files else None)
        download_url = self._safe_text(selected.get("link") if isinstance(selected, dict) else "")
        if not source_id or not source_url or not preview_url or not download_url:
            return None
        user = item.get("user", {}) if isinstance(item.get("user"), dict) else {}
        creator = self._safe_text(user.get("name"))
        creator_url = self._safe_text(user.get("url"))
        title = self._safe_text(item.get("title"), f"Pexels video {source_id}")
        width = int(item.get("width") or (selected.get("width") if isinstance(selected, dict) else 0) or 0)
        height = int(item.get("height") or (selected.get("height") if isinstance(selected, dict) else 0) or 0)
        return {
            "provider": self.name,
            "source": self.name,
            "source_id": source_id,
            "source_url": source_url,
            "preview_url": preview_url,
            "download_url": download_url,
            "media_type": "video",
            "title": title,
            "description": self._safe_text(item.get("url"), title),
            "creator": creator,
            "date": "",
            "license": "Pexels License",
            "attribution_requirements": creator,
            "usage_notes": "Stock media from Pexels. Verify suitability for documentary context.",
            "copyright_status": "licensed",
            "source_type": "stock",
            "source_category": "generic_stock_footage",
            "dimensions": {"width": width, "height": height},
            "duration_seconds": int(item.get("duration") or 0) or None,
            "attribution": {
                "author": creator,
                "author_url": creator_url,
            },
            "license_metadata": {
                "name": "Pexels License",
                "url": "https://www.pexels.com/license/",
            },
            "scene_linkage": {"scene_id": scene_id, "shot_id": shot_id},
            "provider_metadata": {
                "pexels_id": source_id,
                "creator_url": creator_url,
            },
        }

    def search(self, query: Any) -> list[dict[str, Any]]:
        topic = self._safe_text(getattr(query, "topic", ""))
        if not topic:
            self.last_status = RuntimeMediaProviderStatus(
                configured=self.enabled,
                key_available=bool(os.environ.get(self.api_key_env)),
                attempted=False,
                candidates_returned=0,
                skipped_reason="query topic is empty",
            )
            return []
        availability = self.availability()
        if not availability["configured"] or not availability["key_available"]:
            self.last_status = RuntimeMediaProviderStatus(
                configured=bool(availability["configured"]),
                key_available=bool(availability["key_available"]),
                attempted=False,
                candidates_returned=0,
                skipped_reason=str(availability.get("skipped_reason", "")),
            )
            return []

        desired_media_type = self._safe_text(getattr(query, "desired_media_type", "image"), "image").lower()
        limit = max(1, min(80, int(getattr(query, "limit_per_source", self.per_query_limit) or self.per_query_limit)))
        scene_id = self._safe_text(getattr(query, "scene_id", ""))
        shot_id = self._safe_text(getattr(query, "shot_id", ""))
        key = str(os.environ.get(self.api_key_env, ""))
        headers = {"Authorization": key, "Accept": "application/json"}
        endpoint = self._video_endpoint(topic, limit) if desired_media_type == "video" else self._image_endpoint(topic, limit)

        try:
            payload = self.transport.get_json(endpoint, headers, self.timeout_seconds)
        except Exception as error:
            message = str(error)
            lowered = message.lower()
            if "timed out" in lowered or "timeout" in lowered:
                reason = f"Pexels request timed out after {self.timeout_seconds}s"
            else:
                reason = f"Pexels provider error: {message}"
            self.last_status = RuntimeMediaProviderStatus(
                configured=True,
                key_available=True,
                attempted=True,
                candidates_returned=0,
                error_reason=reason,
            )
            raise RuntimeError(reason) from error

        rows = payload.get("videos", []) if desired_media_type == "video" else payload.get("photos", [])
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_previews: set[str] = set()
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            candidate = self._normalize_video(item, scene_id=scene_id, shot_id=shot_id) if desired_media_type == "video" else self._normalize_image(item, scene_id=scene_id, shot_id=shot_id)
            if not candidate:
                continue
            source_id = str(candidate.get("source_id", ""))
            preview_url = str(candidate.get("preview_url", ""))
            if source_id in seen_ids or preview_url in seen_previews:
                continue
            seen_ids.add(source_id)
            seen_previews.add(preview_url)
            normalized.append(candidate)
            if len(normalized) >= limit:
                break

        self.last_status = RuntimeMediaProviderStatus(
            configured=True,
            key_available=True,
            attempted=True,
            candidates_returned=len(normalized),
        )
        return normalized
