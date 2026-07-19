from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from inside_case_factory.core.media import add_image_asset, load_media_manifest
from inside_case_factory.core.project import slugify
from inside_case_factory.core.relevance import media_threshold, project_context, rights_status, topic_relevance
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
    limit_per_source: int = 6


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


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
    candidate_terms = _terms(
        str(candidate.get("title", "")),
        str(candidate.get("creator", "")),
        str(candidate.get("description", "")),
        str(candidate.get("source", "")),
    )
    relevance = min(1.0, len(query_terms & candidate_terms) / max(1, min(5, len(query_terms))))
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

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        search_query = " ".join(part for part in [query.topic, query.people, query.locations, query.dates, query.events] if part)
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search_query,
            "gsrnamespace": "6",
            "gsrlimit": str(query.limit_per_source),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime|sha1|size",
            "iiurlwidth": "640",
        }
        data = _get_json(f"{self.api_url}?{urlencode(params)}")
        pages = data.get("query", {}).get("pages", {})
        candidates = []
        if isinstance(pages, dict):
            for page in pages.values():
                if not isinstance(page, dict):
                    continue
                imageinfo = page.get("imageinfo", [])
                info = imageinfo[0] if imageinfo and isinstance(imageinfo[0], dict) else {}
                metadata = info.get("extmetadata", {}) if isinstance(info.get("extmetadata"), dict) else {}
                title = str(page.get("title", ""))
                source_url = _metadata_value(metadata, "ObjectURL") or str(info.get("descriptionurl", ""))
                license_text = _metadata_value(metadata, "LicenseShortName") or _metadata_value(metadata, "UsageTerms")
                candidates.append(
                    {
                        "source": self.name,
                        "source_id": title,
                        "title": _metadata_value(metadata, "ObjectName") or title.replace("File:", ""),
                        "creator": _metadata_value(metadata, "Artist") or _metadata_value(metadata, "Credit"),
                        "date": _metadata_value(metadata, "DateTimeOriginal") or _metadata_value(metadata, "DateTime"),
                        "license": license_text,
                        "attribution_requirements": _metadata_value(metadata, "AttributionRequired") or _metadata_value(metadata, "Credit"),
                        "usage_notes": _metadata_value(metadata, "UsageTerms"),
                        "source_url": source_url,
                        "preview_url": str(info.get("thumburl") or info.get("url") or ""),
                        "description": _metadata_value(metadata, "ImageDescription"),
                        "copyright_status": copyright_status(license_text, _metadata_value(metadata, "UsageTerms")),
                        "provider_metadata": {"mime": info.get("mime", ""), "sha1": info.get("sha1", "")},
                    }
                )
        return candidates


class InternetArchiveConnector(ArchiveConnector):
    name = "internet_archive"
    search_url = "https://archive.org/advancedsearch.php"

    def search(self, query: DiscoveryQuery) -> list[dict[str, Any]]:
        search_query = " ".join(part for part in [query.topic, query.people, query.locations, query.dates, query.events] if part)
        params = {
            "q": f'({search_query}) AND mediatype:(image)',
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
    connectors: list[ArchiveConnector] = [WikimediaCommonsConnector(), InternetArchiveConnector()]
    if project_root is not None:
        connectors.append(ResearchSourceImageConnector(project_root))
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
            if not preview_url:
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
            preview_path = previews_dir / f"{base_id}.jpg"
            if not _download(preview_url, preview_path):
                errors.append({"source": str(candidate.get("source", "")), "error": f"Could not download preview: {preview_url}"})
                continue
            sha = file_sha256(preview_path)
            fingerprint = byte_fingerprint(preview_path)
            duplicate_of = known_hashes.get(sha, "")
            duplicate_kind = "exact" if duplicate_of else ""
            if not duplicate_of:
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
            if scene_id:
                break

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
    """Use real providers in order until every scene has a relevant review asset."""
    connectors = connectors or default_connectors(project_root)
    scenes_data = read_json(project_root / "manifests" / "scenes.json")
    scenes = [item for item in scenes_data.get("scenes", []) if isinstance(item, dict)]
    director_path = project_root / "manifests" / "director_plan.json"
    director_data = read_json(director_path) if director_path.exists() else {}
    directed = {str(item.get("scene_id")): item for item in director_data.get("scenes", []) if isinstance(item, dict)}
    attempts: list[dict[str, Any]] = []
    uncovered: list[str] = []
    used_providers: set[str] = set()
    for scene in scenes:
        scene_id = str(scene.get("id", ""))
        current_assets = [item for item in load_media_manifest(project_root).get("assets", []) if isinstance(item, dict)]
        if any(scene_id in {str(value) for value in item.get("mapped_scenes", [])} and (project_root / str(item.get("path", ""))).is_file() for item in current_assets):
            used_providers.update(str(item.get("discovery", {}).get("source", "")) for item in current_assets if scene_id in {str(value) for value in item.get("mapped_scenes", [])})
            continue
        direction = directed.get(scene_id, {})
        query_values = [str(value).strip() for value in (
            direction.get("media_search_queries", [])
            or scene.get("archival_media_queries", [])
        ) if str(value).strip()]
        query_values.extend(str(value).strip() for value in direction.get("alternative_media_queries", []) if str(value).strip())
        fallback_query = compact_whitespace(" ".join([
            str(scene.get("heading", "")), *map(str, scene.get("people", [])),
            *map(str, scene.get("locations", [])), *map(str, scene.get("events", [])),
        ]))
        if fallback_query:
            query_values.append(fallback_query)
        query_values = list(dict.fromkeys(query_values))
        found = False
        for connector in connectors:
            for topic in query_values:
                result = discover_archival_media(
                    project_root,
                    DiscoveryQuery(topic=topic, limit_per_source=limit_per_source),
                    [connector], scene_id=scene_id,
                )
                attempts.append({"scene_id": scene_id, "provider": connector.name, "query": topic, "added_count": result["added_count"], "errors": result["errors"]})
                if result["added_count"]:
                    used_providers.add(connector.name)
                    found = True
                    break
            if found:
                break
        if not found:
            uncovered.append(scene_id)
    manifest = load_media_manifest(project_root)
    assets = [item for item in manifest.get("assets", []) if isinstance(item, dict)]
    result = {
        "version": 2, "created_at": datetime.now(UTC).isoformat(),
        "provider_order": [item.name for item in connectors], "providers_used": sorted(used_providers),
        "scene_count": len(scenes), "asset_count": len(assets), "uncovered_scenes": uncovered,
        "attempts": attempts,
    }
    write_json(project_root / "manifests" / DISCOVERY_MANIFEST_NAME, result)
    if uncovered:
        errors = [f"{item['provider']}: {error['error']}" for item in attempts for error in item.get("errors", [])]
        detail = "; ".join(errors[-6:]) or "no relevant result"
        raise RuntimeError(f"All configured real media providers failed for scenes {', '.join(uncovered)}: {detail}")
    return result
