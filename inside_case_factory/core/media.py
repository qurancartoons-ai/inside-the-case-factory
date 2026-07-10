from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any

from inside_case_factory.core.project import slugify
from inside_case_factory.utils.files import read_json, write_json


MEDIA_MANIFEST_VERSION = 1
MEDIA_MANIFEST_NAME = "media_sources.json"


def default_media_manifest() -> dict[str, object]:
    return {
        "version": MEDIA_MANIFEST_VERSION,
        "description": (
            "Manually curated media sources for this project. Add licensed or owned images "
            "here before generation to have the render pipeline use them for mapped scenes."
        ),
        "assets": [],
    }


def media_manifest_path(project_root: Path) -> Path:
    return project_root / "manifests" / MEDIA_MANIFEST_NAME


def ensure_media_manifest(project_root: Path) -> Path:
    path = media_manifest_path(project_root)
    if not path.exists():
        write_json(path, default_media_manifest())
    return path


def load_media_manifest(project_root: Path) -> dict[str, Any]:
    path = ensure_media_manifest(project_root)
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        manifest = default_media_manifest()
    manifest.setdefault("version", MEDIA_MANIFEST_VERSION)
    manifest.setdefault("assets", [])
    return manifest


def save_media_manifest(project_root: Path, manifest: dict[str, Any]) -> Path:
    path = media_manifest_path(project_root)
    manifest.setdefault("version", MEDIA_MANIFEST_VERSION)
    manifest.setdefault("assets", [])
    write_json(path, manifest)
    return path


def _unique_asset_path(images_dir: Path, source_path: Path, media_id: str) -> Path:
    suffix = source_path.suffix.lower() or ".jpg"
    candidate = images_dir / f"{media_id}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = images_dir / f"{media_id}-{counter}{suffix}"
        counter += 1
    return candidate


def add_image_asset(
    project_root: Path,
    image_path: Path,
    *,
    source_url: str = "",
    credit: str = "",
    license_notes: str = "",
    usage_notes: str = "",
    scene_relevance: str = "",
    scene_ids: list[str] | None = None,
    media_id: str | None = None,
    review_status: str = "approved",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not image_path.is_file():
        raise FileNotFoundError(f"Image does not exist: {image_path}")

    manifest = load_media_manifest(project_root)
    assets = manifest.setdefault("assets", [])
    if not isinstance(assets, list):
        assets = []
        manifest["assets"] = assets

    base_id = media_id or slugify(image_path.stem)
    existing_ids = {str(asset.get("id")) for asset in assets if isinstance(asset, dict)}
    resolved_id = base_id
    counter = 2
    while resolved_id in existing_ids:
        resolved_id = f"{base_id}-{counter}"
        counter += 1

    images_dir = project_root / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_asset_path(images_dir, image_path, resolved_id)
    shutil.copy2(image_path, destination)

    asset = {
        "id": resolved_id,
        "type": "image",
        "path": str(destination.relative_to(project_root)),
        "source_url": source_url,
        "credit": credit,
        "license_notes": license_notes,
        "usage_notes": usage_notes,
        "scene_relevance": scene_relevance,
        "mapped_scenes": scene_ids or [],
        "review_status": review_status,
        "added_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        asset.update(extra)
    assets.append(asset)
    save_media_manifest(project_root, manifest)
    return asset


def update_image_review(project_root: Path, media_id: str, review_status: str) -> dict[str, Any] | None:
    manifest = load_media_manifest(project_root)
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and str(asset.get("id")) == media_id:
            asset["review_status"] = review_status
            asset["reviewed_at"] = datetime.now(UTC).isoformat()
            if review_status == "approved" and not asset.get("mapped_scenes"):
                suggested = asset.get("suggested_scenes", [])
                if isinstance(suggested, list):
                    asset["mapped_scenes"] = [str(item) for item in suggested]
            save_media_manifest(project_root, manifest)
            return asset
    return None


def image_for_scene(project_root: Path, scene_id: str) -> dict[str, Any] | None:
    manifest = load_media_manifest(project_root)
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        return None

    fallback: dict[str, Any] | None = None
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        if str(asset.get("review_status", "approved")) != "approved":
            continue
        relative_path = str(asset.get("path", ""))
        if not relative_path:
            continue
        path = project_root / relative_path
        if not path.is_file():
            continue
        mapped_scenes = asset.get("mapped_scenes", [])
        if mapped_scenes == ["*"] and fallback is None:
            fallback = asset
        if isinstance(mapped_scenes, list) and scene_id in {str(item) for item in mapped_scenes}:
            return asset
    return fallback
