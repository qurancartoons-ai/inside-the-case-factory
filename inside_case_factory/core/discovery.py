from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import tomllib
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from inside_case_factory.core.media import add_image_asset, load_media_manifest, save_media_manifest
from inside_case_factory.core.project import slugify
from inside_case_factory.core.relevance import media_threshold, project_context, rights_status, topic_relevance
from inside_case_factory.providers.runtime_media import PexelsStockMediaProvider
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace


USER_AGENT = "InsideTheCaseFactory/0.1 archival-media-discovery (local research tool)"
DISCOVERY_MANIFEST_NAME = "media_discovery.json"
FREE_LICENSE_HINTS = ("public domain", "cc0", "cc-by", "cc by", "cc-by-sa", "cc by-sa")
RESTRICTIVE_HINTS = ("copyright", "rights reserved", "non-commercial", "noncommercial", "no derivatives")


@dataclass(frozen=True)
class DiscoveryQuery:
    topic: str
    people: str = ""
    locations: str = ""
    dates: str = ""
    events: str = ""
    desired_media_type: str = "image"
    shot_id: str = ""
    composition: str = "single_frame"
    content_reason: str = ""
    limit_per_source: int = 6


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(float(error.headers.get("Retry-After", attempt + 1)))
    return {}


def _download(url: str, path: Path) -> bool:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as response:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.read())
            return True
        except (OSError, URLError):
            if attempt < 2:
                time.sleep(attempt + 1)
    return False


def _strip_html(value: str) -> str:
    return compact_whitespace(re.sub(r"<[^>]+>", " ", unescape(value or "")))


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key, {})
    if isinstance(value, dict):
        return _strip_html(str(value.get("value", "")))
    return _strip_html(str(value or ""))


def _terms(*values: str) -> set[str]:
    joined = " ".join(values).lower()
    return {token for token in re.findall(r"[a-z0-9]{3,}", joined)}


def _short_narration_fragment(narration: str, *, max_words: int = 16) -> str:
    tokens = re.findall(r"[a-z0-9]+", narration.lower())
    if not tokens:
        return ""
    return " ".join(tokens[:max_words])


def _scene_specific_queries(scene: dict[str, Any], intent: dict[str, Any]) -> list[str]:
    subject = compact_whitespace(str(intent.get("subject", "")))
    people = [compact_whitespace(str(item)) for item in intent.get("people", []) if str(item).strip()]
    locations = [compact_whitespace(str(item)) for item in intent.get("locations", []) if str(item).strip()]
    periods = [compact_whitespace(str(item)) for item in intent.get("time_period", []) if str(item).strip()]
    events = [compact_whitespace(str(item)) for item in intent.get("event", []) if str(item).strip()]
    visual_requirements = compact_whitespace(str(scene.get("media_requirements", "")))
    content_reason = compact_whitespace(str(intent.get("content_reason", "")))
    narration_fragment = _short_narration_fragment(str(scene.get("narration", "")))
    replacement_goal = compact_whitespace(str(scene.get("replacement_footage_should_communicate", "")))
    action_occurring = compact_whitespace(str(scene.get("action_occurring", "")))
    where_it_takes_place = compact_whitespace(str(scene.get("where_it_takes_place", "")))

    queries: list[str] = []
    queries.extend(compact_whitespace(str(value)) for value in intent.get("search_terms", []) if str(value).strip())
    queries.extend(compact_whitespace(str(value)) for value in intent.get("aliases", []) if str(value).strip())

    if subject and events:
        queries.append(compact_whitespace(f"{subject} {events[0]}"))
    if subject and locations:
        queries.append(compact_whitespace(f"{subject} {locations[0]}"))
    if events and periods:
        queries.append(compact_whitespace(f"{events[0]} {periods[0]} archival"))
    if people and events:
        queries.append(compact_whitespace(f"{' '.join(people[:2])} {events[0]}"))
    if visual_requirements:
        queries.append(visual_requirements)
    if content_reason:
        queries.append(content_reason)
    if narration_fragment:
        queries.append(compact_whitespace(f"{subject} {narration_fragment}" if subject else narration_fragment))
    if action_occurring:
        queries.append(compact_whitespace(f"{subject} {action_occurring}" if subject else action_occurring))
    if where_it_takes_place:
        queries.append(compact_whitespace(f"{subject} {where_it_takes_place}" if subject else where_it_takes_place))
    if replacement_goal:
        queries.append(replacement_goal)

    aggregate = compact_whitespace(" ".join([
        subject,
        " ".join(people),
        " ".join(locations),
        " ".join(periods),
        " ".join(events),
        visual_requirements,
    ]))
    if aggregate:
        queries.append(aggregate)

    deduped: list[str] = []
    seen: set[str] = set()
    for value in queries:
        cleaned = compact_whitespace(value)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned[:180])
    return deduped


def _repo_root_for_project(project_root: Path) -> Path:
    for candidate in [project_root, *project_root.parents]:
        config_path = candidate / "config" / "providers.toml"
        if config_path.exists():
            return candidate
    return Path.cwd()


def _media_provider_settings(project_root: Path, provider_name: str) -> dict[str, Any]:
    config_path = _repo_root_for_project(project_root) / "config" / "providers.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    media = config.get("media", {}) if isinstance(config, dict) else {}
    providers = media.get("providers", {}) if isinstance(media, dict) else {}
    provider_config = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
    return dict(provider_config) if isinstance(provider_config, dict) else {}


def copyright_status(license_text: str, usage_notes: str = "") -> str:
    haystack = f"{license_text} {usage_notes}".lower()
    if any(hint in haystack for hint in FREE_LICENSE_HINTS):
        return "likely_open"
    if any(hint in haystack for hint in RESTRICTIVE_HINTS):
        return "restrictive_or_unknown"
    return "unknown"


def scene_texts(project_root: Path) -> dict[str, str]:
    scenes_path = project_root / "manifests" / "scenes.json"
    if not scenes_path.exists():
        return {"s01": ""}
    data = read_json(scenes_path)
    scenes = data.get("scenes", []) if isinstance(data, dict) else []
    output: dict[str, str] = {}
    if isinstance(scenes, list):
        for scene in scenes:
            if isinstance(scene, dict):
                scene_id = str(scene.get("id", ""))
                output[scene_id] = " ".join(
                    str(scene.get(key, "")) for key in ("heading", "narration", "visual_summary")
                )
    return output or {"s01": ""}


def rank_candidate(candidate: dict[str, Any], query: DiscoveryQuery, scenes: dict[str, str]) -> tuple[float, list[str]]:
    query_terms = _terms(query.topic, query.people, query.locations, query.dates, query.events)
    event_terms = _terms(query.events)
    location_terms = _terms(query.locations)
    people_terms = _terms(query.people)
    candidate_terms = _terms(
        str(candidate.get("title", "")),
        str(candidate.get("creator", "")),
        str(candidate.get("description", "")),
        str(candidate.get("source", "")),
    )
    relevance = min(1.0, len(query_terms & candidate_terms) / max(1, min(5, len(query_terms))))
    if event_terms and not (event_terms & candidate_terms):
        relevance *= 0.72
    if location_terms and not (location_terms & candidate_terms):
        relevance *= 0.85
    if people_terms and (people_terms & candidate_terms) and event_terms and not (event_terms & candidate_terms):
        # Penalize generic person-only imagery when event evidence is requested.
        relevance *= 0.65
    boosted_markers = {
        "archive",
        "archival",
        "news",
        "headline",
        "court",
        "hospital",
        "ambulance",
        "explosion",
        "interview",
        "document",
    }
    if boosted_markers & candidate_terms:
        relevance = min(1.0, relevance + 0.08)
    suggested: list[tuple[float, str]] = []
    for scene_id, text in scenes.items():
        scene_terms = _terms(text)
        score = len(scene_terms & candidate_terms) / max(1, min(5, len(scene_terms)))
        if score:
            suggested.append((score, scene_id))
    suggested.sort(reverse=True)
    if suggested:
        relevance = min(1.0, relevance * 0.7 + suggested[0][0] * 0.3)
    return round(relevance, 3), [scene_id for _, scene_id in suggested[:2]]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def byte_fingerprint(path: Path) -> str:
    data = path.read_bytes()
    if not data:
        return ""
    buckets = 16
    step = max(1, len(data) // buckets)
    values = [sum(data[index : index + step]) // max(1, len(data[index : index + step])) for index in range(0, len(data), step)]
    avg = sum(values) / len(values)
    return "".join("1" if value >= avg else "0" for value in values[:buckets])


def hamming_distance(left: str, right: str) -> int:
    if not left or not right or len(left) != len(right):
        return 999
    return sum(1 for a, b in zip(left, right, strict=True) if a != b)


class ArchiveConnector:
    name = "archive"

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        raise NotImplementedError


class WikimediaCommonsConnector(ArchiveConnector):
    name = "wikimedia_commons"
    api_url = "https://commons.wikimedia.org/w/api.php"

    @staticmethod
    def _candidates(pages: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = []
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            imageinfo = page.get("imageinfo", [])
            info = imageinfo[0] if imageinfo and isinstance(imageinfo[0], dict) else {}
            videoinfo = page.get("videoinfo", [])
            video_info = videoinfo[0] if videoinfo and isinstance(videoinfo[0], dict) else {}
            if video_info:
                info = {**info, **video_info}
            metadata = info.get("extmetadata", {}) if isinstance(info.get("extmetadata"), dict) else {}
            title = str(page.get("title", ""))
            source_url = _metadata_value(metadata, "ObjectURL") or str(info.get("descriptionurl", ""))
            license_text = _metadata_value(metadata, "LicenseShortName") or _metadata_value(metadata, "UsageTerms")
            extension = Path(str(info.get("url", "")).split("?", 1)[0]).suffix.lower()
            derivatives = [item for item in video_info.get("derivatives", []) if isinstance(item, dict)]
            playable = [item for item in derivatives if "webm" in str(item.get("type", "")) and 640 <= int(item.get("width", 0) or 0) <= 1280]
            playable.sort(key=lambda item: int(item.get("width", 0) or 0), reverse=True)
            playable_url = str(playable[0].get("src", "")) if playable else str(info.get("url") or "")
            candidates.append({
                "source": "wikimedia_commons", "source_id": title,
                "title": _metadata_value(metadata, "ObjectName") or title.replace("File:", ""),
                "creator": _metadata_value(metadata, "Artist") or _metadata_value(metadata, "Credit"),
                "date": _metadata_value(metadata, "DateTimeOriginal") or _metadata_value(metadata, "DateTime"),
                "license": license_text, "attribution_requirements": _metadata_value(metadata, "AttributionRequired") or _metadata_value(metadata, "Credit"),
                "usage_notes": _metadata_value(metadata, "UsageTerms"), "source_url": source_url,
                "preview_url": str(info.get("thumburl") or playable_url), "download_url": playable_url,
                "media_type": "video" if str(info.get("mime", "")).startswith("video/") or extension in {".webm", ".ogv", ".mp4", ".mov"} else "image",
                "description": _metadata_value(metadata, "ImageDescription"),
                "copyright_status": copyright_status(license_text, _metadata_value(metadata, "UsageTerms")),
                "provider_metadata": {"mime": info.get("mime", ""), "sha1": info.get("sha1", ""), "width": info.get("width"), "height": info.get("height")},
            })
        return candidates

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        search_query = " ".join(part for part in [query.topic, query.people, query.locations, query.dates, query.events] if part)
        if query.desired_media_type == "video":
            search_query = f"{search_query} filetype:video"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search_query,
            "gsrnamespace": "6",
            "gsrlimit": str(query.limit_per_source),
            "prop": "imageinfo|videoinfo",
            "iiprop": "url|extmetadata|mime|sha1|size",
            "iiurlwidth": "640",
            "viprop": "url|derivatives",
        }
        data = _get_json(f"{self.api_url}?{urlencode(params)}")
        pages = data.get("query", {}).get("pages", {})
        return self._candidates(pages) if isinstance(pages, dict) else []


class WikimediaCategoryConnector(WikimediaCommonsConnector):
    """Expand a topic-matched Commons category once and reuse it across shot searches."""

    name = "wikimedia_commons_category"

    def __init__(self) -> None:
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        topic_terms = _terms(query.topic, query.people, query.events)
        cache_key = " ".join(sorted(topic_terms))
        if cache_key in self._cache:
            return self._cache[cache_key][: query.limit_per_source]
        search_params = {"action": "query", "format": "json", "list": "search", "srsearch": query.topic, "srnamespace": "14", "srlimit": "5"}
        categories = _get_json(f"{self.api_url}?{urlencode(search_params)}").get("query", {}).get("search", [])
        ranked = sorted(
            (item for item in categories if isinstance(item, dict)),
            key=lambda item: len(topic_terms & _terms(str(item.get("title", "")))), reverse=True,
        )
        category = str(ranked[0].get("title", "")) if ranked and len(topic_terms & _terms(str(ranked[0].get("title", "")))) >= 2 else ""
        if not category:
            self._cache[cache_key] = []
            return []
        params = {
            "action": "query", "format": "json", "generator": "categorymembers", "gcmtitle": category,
            "gcmtype": "file", "gcmlimit": str(max(30, query.limit_per_source)), "prop": "imageinfo|videoinfo",
            "iiprop": "url|extmetadata|mime|sha1|size", "iiurlwidth": "1280", "viprop": "url|derivatives",
        }
        pages = _get_json(f"{self.api_url}?{urlencode(params)}").get("query", {}).get("pages", {})
        found = self._candidates(pages) if isinstance(pages, dict) else []
        for item in found:
            item["source"] = self.name
            item["provider_metadata"] = {**item.get("provider_metadata", {}), "category": category}
        self._cache[cache_key] = found
        return found[: query.limit_per_source]


class InternetArchiveConnector(ArchiveConnector):
    name = "internet_archive"
    search_url = "https://archive.org/advancedsearch.php"

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        search_query = " ".join(part for part in [query.topic, query.people, query.locations, query.dates, query.events] if part)
        media_type = "movies" if query.desired_media_type == "video" else "image"
        params = {
            "q": f'({search_query}) AND mediatype:({media_type})',
            "fl[]": ["identifier", "title", "creator", "date", "licenseurl", "description"],
            "rows": str(query.limit_per_source),
            "page": "1",
            "output": "json",
        }
        query_pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, list):
                query_pairs.extend((key, item) for item in value)
            else:
                query_pairs.append((key, value))
        data = _get_json(f"{self.search_url}?{urlencode(query_pairs)}")
        docs = data.get("response", {}).get("docs", [])
        candidates = []
        if isinstance(docs, list):
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                identifier = str(doc.get("identifier", ""))
                if not identifier:
                    continue
                title = str(doc.get("title", identifier))
                license_text = str(doc.get("licenseurl", ""))
                download_url = ""
                if query.desired_media_type == "video":
                    try:
                        metadata = _get_json(f"https://archive.org/metadata/{quote(identifier)}")
                        files = [item for item in metadata.get("files", []) if isinstance(item, dict)]
                        playable = [item for item in files if str(item.get("name", "")).lower().endswith((".mp4", ".webm"))]
                        playable.sort(key=lambda item: int(item.get("size", 0) or 0))
                        selected = next((item for item in playable if 0 < int(item.get("size", 0) or 0) <= 100_000_000), None)
                        if selected:
                            download_url = f"https://archive.org/download/{quote(identifier)}/{quote(str(selected['name']))}"
                    except Exception:
                        download_url = ""
                candidates.append(
                    {
                        "source": self.name,
                        "source_id": identifier,
                        "title": title,
                        "creator": str(doc.get("creator", "")),
                        "date": str(doc.get("date", "")),
                        "license": license_text,
                        "attribution_requirements": "Check the Internet Archive item metadata and files before publication.",
                        "usage_notes": "Internet Archive item metadata may be user-supplied; review rights before use.",
                        "source_url": f"https://archive.org/details/{quote(identifier)}",
                        "preview_url": f"https://archive.org/services/img/{quote(identifier)}",
                        "download_url": download_url,
                        "media_type": "video" if download_url else "image",
                        "description": _strip_html(str(doc.get("description", ""))),
                        "copyright_status": copyright_status(license_text, str(doc.get("description", ""))),
                        "provider_metadata": {},
                    }
                )
        return candidates


class ResearchSourceImageConnector(ArchiveConnector):
    """Use the lead image from already retrieved, topic-matched research pages."""

    name = "research_source_pages"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        sources = read_json(self.project_root / "manifests" / "sources.json").get("sources", [])
        claims = read_json(self.project_root / "manifests" / "claims.json").get("claims", [])
        query_terms = _terms(query.topic)
        candidates: list[dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("id", ""))
            linked_claim_text = " ".join(str(claim.get("text", "")) for claim in claims if isinstance(claim, dict) and source_id in {str(value) for value in claim.get("source_ids", [])})
            if len(query_terms & _terms(str(source.get("title", "")), source_id, linked_claim_text)) < 1:
                continue
            source_url = str(source.get("url", ""))
            if not source_url:
                continue
            try:
                request = Request(source_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
                with urlopen(request, timeout=30) as response:
                    html = response.read(1_500_000).decode("utf-8", errors="replace")
            except (OSError, URLError):
                continue
            match = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', html, re.I)
            if not match:
                match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', html, re.I)
            if not match:
                continue
            preview_url = unescape(match.group(1))
            candidates.append({
                "source": self.name, "source_id": source_id,
                "title": str(source.get("title", "")), "creator": str(source.get("publisher", "")),
                "date": str(source.get("publication_date", "")), "license": "Rights status requires separate review",
                "usage_notes": "Lead image from the cited research source; verify publication rights separately.",
                "source_url": source_url, "preview_url": preview_url,
                "description": f"Lead image for the research source: {source.get('title', '')}",
                "copyright_status": "unknown", "provider_metadata": {"research_source_id": source.get("id", "")},
            })
        return candidates[: query.limit_per_source]


def default_connectors(project_root: Path | None = None) -> list[ArchiveConnector]:
    connectors: list[ArchiveConnector] = [InternetArchiveConnector(), WikimediaCategoryConnector(), WikimediaCommonsConnector()]
    if project_root is not None:
        connectors.append(ResearchSourceImageConnector(project_root))
        pexels_settings = _media_provider_settings(project_root, "pexels")
        connectors.append(
            PexelsStockMediaProvider(
                enabled=bool(pexels_settings.get("enabled", False)),
                api_key_env=str(pexels_settings.get("api_key_env", "PEXELS_API_KEY")),
                timeout_seconds=int(pexels_settings.get("timeout_seconds", 20)),
                per_query_limit=int(pexels_settings.get("per_query_limit", 6)),
            )
        )
    return connectors


def discover_archival_media(
    project_root: Path,
    query: DiscoveryQuery,
    connectors: list[ArchiveConnector] | None = None,
    *,
    scene_id: str = "",
) -> dict[str, Any]:
    connectors = connectors or default_connectors(project_root)
    previews_dir = project_root / "assets" / "images" / "discovered"
    scenes = scene_texts(project_root)
    if scene_id:
        scenes = {scene_id: scenes.get(scene_id, "")}
    manifest = load_media_manifest(project_root)
    existing = [asset for asset in manifest.get("assets", []) if isinstance(asset, dict)]
    known_hashes = {str(asset.get("sha256", "")): str(asset.get("id", "")) for asset in existing if asset.get("sha256")}
    known_fingerprints = {str(asset.get("fingerprint", "")): str(asset.get("id", "")) for asset in existing if asset.get("fingerprint")}
    added: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    context = project_context(project_root)
    threshold = media_threshold(project_root)
    filtered_count = duplicate_count = 0

    for connector in connectors:
        try:
            candidates = connector.search(query)
        except Exception as error:
            errors.append({"source": connector.name, "error": str(error)})
            continue
        for candidate in candidates:
            preview_url = str(candidate.get("preview_url", ""))
            media_type = str(candidate.get("media_type", "image"))
            download_url = str(candidate.get("download_url", "")) if media_type == "video" else preview_url
            if not preview_url or not download_url:
                continue
            scene_score, suggested_scenes = rank_candidate(candidate, query, scenes)
            relevance = topic_relevance(context, candidate)
            project_score = relevance["score"]
            score = round(max(scene_score, float(project_score or 0.0)), 3)
            if scene_score >= threshold:
                relevance = {
                    **relevance,
                    "reason": f"Scene-inhoudelijke match met zoekopdracht '{query.topic}'. {relevance['reason']}",
                    "matched": list(dict.fromkeys([query.topic, *relevance["matched"]])),
                }
            if scene_id and score is not None and score >= threshold:
                suggested_scenes = [scene_id]
            if score is None or score < threshold or not suggested_scenes:
                filtered_count += 1
                continue
            base_id = slugify(f"{candidate.get('source')}-{candidate.get('source_id') or candidate.get('title')}")
            suffix = Path(download_url.split("?", 1)[0]).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm", ".ogv", ".mov"}:
                suffix = ".mp4" if media_type == "video" else ".jpg"
            preview_path = previews_dir / f"{base_id}{suffix}"
            downloaded = _download(download_url, preview_path)
            original_url = str(candidate.get("download_url", ""))
            if not downloaded and original_url and original_url != download_url:
                downloaded = _download(original_url, preview_path)
            if not downloaded:
                errors.append({"source": str(candidate.get("source", "")), "error": f"Could not download media: {download_url}"})
                continue
            sha = file_sha256(preview_path)
            fingerprint = byte_fingerprint(preview_path)
            duplicate_of = known_hashes.get(sha, "")
            duplicate_kind = "exact" if duplicate_of else ""
            if not duplicate_of and media_type != "video":
                for known_fingerprint, known_id in known_fingerprints.items():
                    if hamming_distance(fingerprint, known_fingerprint) <= 2:
                        duplicate_of = known_id
                        duplicate_kind = "near"
                        break
            if duplicate_of:
                duplicate_count += 1
                preview_path.unlink(missing_ok=True)
                continue
            extra = {
                "discovery": {
                    "source": candidate.get("source", ""),
                    "source_id": candidate.get("source_id", ""),
                    "discovered_at": datetime.now(UTC).isoformat(),
                },
                "title": candidate.get("title", ""),
                "creator": candidate.get("creator", ""),
                "date": candidate.get("date", ""),
                "license": candidate.get("license", ""),
                "attribution_requirements": candidate.get("attribution_requirements", ""),
                "copyright_status": candidate.get("copyright_status", "unknown"),
                "preview_url": preview_url,
                "provider": candidate.get("provider", candidate.get("source", "")),
                "source_type": candidate.get("source_type", ""),
                "source_category": candidate.get("source_category", ""),
                "dimensions": candidate.get("dimensions", {}),
                "duration_seconds": candidate.get("duration_seconds"),
                "attribution": candidate.get("attribution", {}),
                "license_metadata": candidate.get("license_metadata", {}),
                "scene_linkage": candidate.get("scene_linkage", {"scene_id": scene_id, "shot_id": query.shot_id}),
                "sha256": sha,
                "fingerprint": fingerprint,
                "duplicate_of": "",
                "duplicate_kind": "",
                "duplicate_confidence": 0.0,
                "topic_relevance": score,
                "scene_relevance_score": scene_score,
                "relevance_score": score,
                "relevance_reason": relevance["reason"],
                "relevance_matches": relevance["matched"],
                "relevance_missing": relevance["missing"],
                "suggested_scenes": suggested_scenes,
                "review_eligible": True,
                "rights_status": rights_status(candidate),
                "project_slug": project_root.name,
                "scene_id": scene_id or (suggested_scenes[0] if len(suggested_scenes) == 1 else ""),
                "content_reason": str(query.events or query.topic),
                "provider_metadata": candidate.get("provider_metadata", {}),
                "type": media_type,
                "shot_ids": [query.shot_id] if query.shot_id else [],
                "desired_media_type": query.desired_media_type,
                "planned_composition": query.composition,
                "shot_relevance_score": scene_score,
                "shot_relevance_reason": f"Match voor {query.shot_id or scene_id}: {query.content_reason or query.events or query.topic}",
                "content_reason": query.content_reason or str(query.events or query.topic),
            }
            asset = add_image_asset(
                project_root,
                preview_path,
                source_url=str(candidate.get("source_url", "")),
                credit=str(candidate.get("creator", "")),
                license_notes=str(candidate.get("license", "")),
                usage_notes=str(candidate.get("usage_notes", "")),
                scene_relevance=str(candidate.get("description", "")),
                scene_ids=suggested_scenes,
                media_id=base_id,
                review_status="pending_review",
                extra=extra,
            )
            added.append(asset)
            known_hashes[sha] = str(asset.get("id", ""))
            known_fingerprints[fingerprint] = str(asset.get("id", ""))

    added.sort(key=lambda item: float(item.get("relevance_score", 0)), reverse=True)
    discovery_manifest = {
        "version": 1,
        "query": query.__dict__,
        "sources": [connector.name for connector in connectors],
        "created_at": datetime.now(UTC).isoformat(),
        "added_count": len(added),
        "filtered_count": filtered_count,
        "duplicate_count": duplicate_count,
        "errors": errors,
        "assets": [
            {
                "id": asset.get("id"),
                "source": asset.get("discovery", {}).get("source", ""),
                "title": asset.get("title", ""),
                "source_url": asset.get("source_url", ""),
                "copyright_status": asset.get("copyright_status", ""),
                "duplicate_of": asset.get("duplicate_of", ""),
                "duplicate_kind": asset.get("duplicate_kind", ""),
                "relevance_score": asset.get("relevance_score", 0),
                "suggested_scenes": asset.get("suggested_scenes", []),
                "review_status": asset.get("review_status", ""),
            }
            for asset in added
        ],
    }
    write_json(project_root / "manifests" / DISCOVERY_MANIFEST_NAME, discovery_manifest)
    return discovery_manifest


def discover_project_scene_media(
    project_root: Path,
    connectors: list[ArchiveConnector] | None = None,
    *,
    limit_per_source: int = 6,
) -> dict[str, Any]:
    """Search real providers per planned shot, preserving intent and failure evidence."""
    connectors = connectors or default_connectors(project_root)
    scenes_data = read_json(project_root / "manifests" / "scenes.json")
    scenes = [item for item in scenes_data.get("scenes", []) if isinstance(item, dict)]
    director_path = project_root / "manifests" / "director_plan.json"
    director_data = read_json(director_path) if director_path.exists() else {}
    directed = {str(item.get("scene_id")): item for item in director_data.get("scenes", []) if isinstance(item, dict)}
    attempts: list[dict[str, Any]] = []
    uncovered: list[str] = []
    used_providers: set[str] = set()
    started_at = time.monotonic()
    budget_seconds = 60.0
    budget_exhausted = False
    availability: dict[str, dict[str, Any]] = {}
    for connector in connectors:
        defaults = {
            "configured": True,
            "key_available": True,
            "attempted": False,
            "candidates_returned": 0,
            "skipped_reason": "",
            "error_reason": "",
        }
        details = connector.availability() if hasattr(connector, "availability") and callable(getattr(connector, "availability")) else {}
        if isinstance(details, dict):
            defaults.update({
                "configured": bool(details.get("configured", defaults["configured"])),
                "key_available": bool(details.get("key_available", defaults["key_available"])),
                "attempted": bool(details.get("attempted", defaults["attempted"])),
                "candidates_returned": int(details.get("candidates_returned", defaults["candidates_returned"])),
                "skipped_reason": str(details.get("skipped_reason", defaults["skipped_reason"])),
                "error_reason": str(details.get("error_reason", defaults["error_reason"])),
            })
        availability[connector.name] = defaults
    # One eligible candidate per shot is enough to advance into review; the
    # render pipeline already has offline-safe fallbacks for uncovered scenes.
    workflow_path = project_root / "manifests" / "workflow.json"
    workflow = read_json(workflow_path) if workflow_path.exists() else {}
    recycle_mode = str(workflow.get("workflow_type", "")) == "recycle_documentary"
    target_per_shot = 3 if recycle_mode else 1
    for scene in scenes:
        if time.monotonic() - started_at >= budget_seconds:
            budget_exhausted = True
            break
        scene_id = str(scene.get("id", ""))
        scene_has_coverage = False
        direction = directed.get(scene_id, {})
        shot_intents = [shot.get("media_intent", {}) for shot in direction.get("shots", []) if isinstance(shot, dict)]
        if not shot_intents:
            shot_intents = [{
                "scene_id": scene_id, "shot_id": f"{scene_id}-shot-1", "subject": str(scene.get("heading", "")),
                "people": scene.get("people", []), "locations": scene.get("locations", []), "time_period": scene.get("dates", []),
                "event": scene.get("events", []), "desired_media_type": "image", "search_terms": scene.get("archival_media_queries", []),
                "aliases": scene.get("alternative_media_queries", []), "composition": "single_frame",
                "content_reason": str(scene.get("media_requirements", "Relevant scene evidence.")),
            }]
        for intent in shot_intents:
            if time.monotonic() - started_at >= budget_seconds:
                uncovered.append(str(intent.get("shot_id", "")))
                budget_exhausted = True
                break
            shot_id = str(intent.get("shot_id", ""))
            current_manifest = load_media_manifest(project_root)
            current_assets = [item for item in current_manifest.get("assets", []) if isinstance(item, dict)]
            linked = [
                item for item in current_assets
                if shot_id in {str(value) for value in item.get("shot_ids", [])}
                and item.get("review_status") != "rejected" and not item.get("duplicate_of")
            ]
            if len(linked) < target_per_shot:
                desired = str(intent.get("desired_media_type", "image"))
                assignable = [
                    item for item in current_assets
                    if item not in linked and item.get("review_status") == "approved" and not item.get("duplicate_of")
                    and float(item.get("relevance_score", 0) or 0) >= media_threshold(project_root)
                    and scene_id in {str(value) for value in item.get("suggested_scenes", [])}
                    and len(item.get("shot_ids", [])) < int(intent.get("maximum_reuse", 1) or 1)
                ]
                assignable.sort(key=lambda item: (
                    str(item.get("type", "image")) != desired,
                    len(item.get("shot_ids", [])),
                    -float(item.get("relevance_score", 0) or 0),
                ))
                for item in assignable[: target_per_shot - len(linked)]:
                    item.setdefault("shot_ids", []).append(shot_id)
                    if scene_id not in {str(value) for value in item.get("suggested_scenes", [])}:
                        item.setdefault("suggested_scenes", []).append(scene_id)
                    item["shot_relevance_score"] = max(float(item.get("shot_relevance_score", 0) or 0), float(item.get("relevance_score", 0) or 0))
                    item["shot_relevance_reason"] = f"Allocated to {shot_id} from the topic-relevant project pool for {intent.get('content_reason', '')}"
                    linked.append(item)
                if linked:
                    save_media_manifest(project_root, current_manifest)
            if len(linked) >= target_per_shot:
                used_providers.update(str(item.get("discovery", {}).get("source", "")) for item in linked)
                scene_has_coverage = True
                continue
            query_values = _scene_specific_queries(scene, intent)
            found_for_shot = len(linked)
            for connector in connectors:
                report = availability.setdefault(connector.name, {
                    "configured": True,
                    "key_available": True,
                    "attempted": False,
                    "candidates_returned": 0,
                    "skipped_reason": "",
                    "error_reason": "",
                })
                if not report["configured"] or not report["key_available"]:
                    if not report["skipped_reason"]:
                        if not report["configured"]:
                            report["skipped_reason"] = "provider disabled by configuration"
                        elif not report["key_available"]:
                            report["skipped_reason"] = "required API key is missing"
                    continue
                for topic in query_values:
                    result = discover_archival_media(project_root, DiscoveryQuery(
                        topic=topic, people=" ".join(map(str, intent.get("people", []))),
                        locations=" ".join(map(str, intent.get("locations", []))), dates=" ".join(map(str, intent.get("time_period", []))),
                        events=" ".join(map(str, intent.get("event", []))), desired_media_type=str(intent.get("desired_media_type", "image")),
                        shot_id=shot_id, composition=str(intent.get("composition", "single_frame")),
                        content_reason=str(intent.get("content_reason", "")), limit_per_source=limit_per_source,
                    ), [connector], scene_id=scene_id)
                    report["attempted"] = True
                    report["candidates_returned"] = int(report.get("candidates_returned", 0)) + int(result.get("added_count", 0) or 0)
                    if result.get("errors") and not report.get("error_reason"):
                        first_error = next((item for item in result["errors"] if isinstance(item, dict) and item.get("error")), None)
                        if first_error:
                            report["error_reason"] = str(first_error.get("error", ""))
                    attempts.append({"scene_id": scene_id, "shot_id": shot_id, "provider": connector.name, "query": topic, "desired_media_type": intent.get("desired_media_type"), "added_count": result["added_count"], "filtered_count": result.get("filtered_count", 0), "duplicate_count": result.get("duplicate_count", 0), "errors": result["errors"]})
                    found_for_shot += int(result["added_count"])
                    if result["added_count"]:
                        used_providers.add(connector.name)
                    if found_for_shot >= target_per_shot:
                        break
                if found_for_shot >= target_per_shot:
                    break
            if found_for_shot:
                scene_has_coverage = True
            if not found_for_shot and not scene_has_coverage:
                uncovered.append(shot_id)
        if budget_exhausted:
            break
    manifest = load_media_manifest(project_root)
    assets = [item for item in manifest.get("assets", []) if isinstance(item, dict)]
    result = {
        "version": 2, "created_at": datetime.now(UTC).isoformat(),
        "provider_order": [item.name for item in connectors], "providers_used": sorted(used_providers),
        "provider_availability": availability,
        "scene_count": len(scenes), "asset_count": len(assets), "uncovered_shots": uncovered,
        "timed_out": budget_exhausted,
        "budget_seconds": budget_seconds,
        "attempts": attempts,
    }
    write_json(project_root / "manifests" / DISCOVERY_MANIFEST_NAME, result)
    if uncovered:
        all_unavailable = bool(availability) and not any(
            bool(item.get("attempted")) or bool(item.get("candidates_returned"))
            for item in availability.values()
        )
        if all_unavailable:
            detail = "; ".join(
                f"{name}: {info.get('skipped_reason') or 'provider unavailable'}"
                for name, info in availability.items()
            )
            raise RuntimeError(f"All configured real media providers are unavailable: {detail}")
        workflow_path = project_root / "manifests" / "workflow.json"
        workflow = read_json(workflow_path) if workflow_path.exists() else {}
        run_quality_mode = str(workflow.get("run_quality_mode", "sample_or_demo"))
        if run_quality_mode == "sample_or_demo":
            result["fallback_mode_used"] = True
            write_json(project_root / "manifests" / DISCOVERY_MANIFEST_NAME, result)
            return result
        errors = [f"{item['provider']}: {error['error']}" for item in attempts for error in item.get("errors", [])]
        detail = "; ".join(errors[-6:]) or "no relevant result"
        raise RuntimeError(f"All configured real media providers failed for shots {', '.join(uncovered)}: {detail}")
    return result
