from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APPROVED_RIGHTS = {"approved", "public_domain", "licensed", "owned", "cc0", "cc-by", "cc-by-sa"}


def rights_are_approved(asset: dict[str, Any]) -> bool:
    if str(asset.get("review_status", "pending_review")) != "approved":
        return False
    rights = str(asset.get("rights_status", asset.get("copyright_status", ""))).lower().replace("_", "-")
    license_text = str(asset.get("license", asset.get("license_notes", ""))).lower()
    normalized_license = license_text.replace("_", "-").replace(" ", "-")
    return rights.replace("-", "_") in {value.replace("-", "_") for value in APPROVED_RIGHTS} or any(
        marker in license_text or marker.replace(" ", "-") in normalized_license
        for marker in ("public domain", "cc0", "cc-by", "creative commons", "owned", "permission", "licensed")
    )


@dataclass(frozen=True)
class VisualAssetCandidate:
    id: str
    kind: str
    provider: str
    path: str
    source_url: str
    license: str
    rights_status: str
    claim_ids: tuple[str, ...]
    mapped_scenes: tuple[str, ...]
    generated: bool = False
    shot_ids: tuple[str, ...] = ()
    relevance_score: float = 0.0
    relevance_reason: str = ""
    duplicate_confidence: float = 0.0
    content_reason: str = ""

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "provider": self.provider, "path": self.path,
            "source_url": self.source_url, "license": self.license, "rights_status": self.rights_status,
            "claim_ids": list(self.claim_ids), "mapped_scenes": list(self.mapped_scenes), "generated": self.generated,
            "shot_ids": list(self.shot_ids), "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason, "duplicate_confidence": self.duplicate_confidence,
            "content_reason": self.content_reason,
        }


class VisualAssetProvider(ABC):
    name: str
    priority: int

    @abstractmethod
    def candidates(self, project_root: Path, scene: dict[str, Any], assets: list[dict[str, Any]]) -> list[VisualAssetCandidate]:
        raise NotImplementedError


class ApprovedArchiveProvider(VisualAssetProvider):
    name = "approved_archive"
    priority = 10

    def candidates(self, project_root: Path, scene: dict[str, Any], assets: list[dict[str, Any]]) -> list[VisualAssetCandidate]:
        scene_id = str(scene.get("id", ""))
        result = []
        for asset in assets:
            if not rights_are_approved(asset) or str(asset.get("type", "image")) not in {"image", "video", "document"}:
                continue
            if not str(asset.get("source_url", "")):
                continue
            mappings = tuple(str(item) for item in asset.get("mapped_scenes", []))
            path = str(asset.get("path", ""))
            if scene_id not in mappings and "*" not in mappings:
                continue
            if not path or not (project_root / path).is_file():
                continue
            result.append(VisualAssetCandidate(
                id=str(asset.get("id")), kind=str(asset.get("type", "image")), provider=self.name, path=path,
                source_url=str(asset.get("source_url", "")), license=str(asset.get("license", asset.get("license_notes", ""))),
                rights_status="approved", claim_ids=tuple(str(item) for item in asset.get("claim_ids", scene.get("claim_ids", []))),
                mapped_scenes=mappings,
                shot_ids=tuple(str(item) for item in asset.get("shot_ids", [])),
                relevance_score=float(asset.get("shot_relevance_score", asset.get("relevance_score", 0)) or 0),
                relevance_reason=str(asset.get("shot_relevance_reason", asset.get("relevance_reason", ""))),
                duplicate_confidence=float(asset.get("duplicate_confidence", 0) or 0),
                content_reason=str(asset.get("content_reason", "")),
            ))
        return result


class LocalMediaProvider(ApprovedArchiveProvider):
    name = "approved_local_media"
    priority = 20

    def candidates(self, project_root: Path, scene: dict[str, Any], assets: list[dict[str, Any]]) -> list[VisualAssetCandidate]:
        scene_id = str(scene.get("id", ""))
        result = []
        for asset in assets:
            if not rights_are_approved(asset) or str(asset.get("source_url", "")):
                continue
            mappings = tuple(str(item) for item in asset.get("mapped_scenes", []))
            path = str(asset.get("path", ""))
            if (scene_id not in mappings and "*" not in mappings) or not path or not (project_root / path).is_file():
                continue
            result.append(VisualAssetCandidate(
                id=str(asset.get("id")), kind=str(asset.get("type", "image")), provider=self.name, path=path,
                source_url="", license=str(asset.get("license", asset.get("license_notes", ""))), rights_status="approved",
                claim_ids=tuple(str(item) for item in asset.get("claim_ids", scene.get("claim_ids", []))), mapped_scenes=mappings,
                shot_ids=tuple(str(item) for item in asset.get("shot_ids", [])),
                relevance_score=float(asset.get("shot_relevance_score", asset.get("relevance_score", 0)) or 0),
                relevance_reason=str(asset.get("shot_relevance_reason", asset.get("relevance_reason", ""))),
                duplicate_confidence=float(asset.get("duplicate_confidence", 0) or 0),
                content_reason=str(asset.get("content_reason", "")),
            ))
        return result


class EvidenceGraphicProvider(VisualAssetProvider):
    name = "evidence_graphics"
    priority = 30

    def candidates(self, project_root: Path, scene: dict[str, Any], assets: list[dict[str, Any]]) -> list[VisualAssetCandidate]:
        scene_id = str(scene.get("id", "scene"))
        kind = "map" if scene.get("locations") else "timeline" if scene.get("dates") else "document"
        return [VisualAssetCandidate(
            id=f"{scene_id}-{kind}", kind=kind, provider=self.name, path="", source_url="",
            license="Internally generated factual graphic", rights_status="owned",
            claim_ids=tuple(str(item) for item in scene.get("claim_ids", [])), mapped_scenes=(scene_id,), generated=True,
        )]


class OfflineGeneratedFallbackProvider(VisualAssetProvider):
    name = "offline_safe_fallback"
    priority = 40

    def candidates(self, project_root: Path, scene: dict[str, Any], assets: list[dict[str, Any]]) -> list[VisualAssetCandidate]:
        scene_id = str(scene.get("id", "scene"))
        return [VisualAssetCandidate(
            id=f"{scene_id}-fallback", kind="generated_documentary_graphic", provider=self.name, path="", source_url="",
            license="Internally generated; owned", rights_status="owned",
            claim_ids=tuple(str(item) for item in scene.get("claim_ids", [])), mapped_scenes=(scene_id,), generated=True,
        )]


DEFAULT_VISUAL_PROVIDERS: tuple[VisualAssetProvider, ...] = (
    ApprovedArchiveProvider(), LocalMediaProvider(), EvidenceGraphicProvider(), OfflineGeneratedFallbackProvider()
)


def resolve_scene_assets(
    project_root: Path,
    scene: dict[str, Any],
    assets: list[dict[str, Any]],
    providers: tuple[VisualAssetProvider, ...] = DEFAULT_VISUAL_PROVIDERS,
) -> list[dict[str, Any]]:
    resolved: list[VisualAssetCandidate] = []
    seen: set[str] = set()
    for provider in sorted(providers, key=lambda item: item.priority):
        for candidate in provider.candidates(project_root, scene, assets):
            if candidate.id not in seen:
                resolved.append(candidate)
                seen.add(candidate.id)
    if not resolved:
        resolved.extend(OfflineGeneratedFallbackProvider().candidates(project_root, scene, assets))
    return [candidate.manifest() for candidate in resolved]


def resolve_shot_assets(
    project_root: Path,
    scene: dict[str, Any],
    shot_id: str,
    assets: list[dict[str, Any]],
    *,
    desired_media_type: str = "",
    providers: tuple[VisualAssetProvider, ...] = DEFAULT_VISUAL_PROVIDERS,
) -> list[dict[str, Any]]:
    """Resolve approved assets for one shot; relevance and rights remain separate."""
    candidates = resolve_scene_assets(project_root, scene, assets, providers)
    concrete = [item for item in candidates if not item.get("generated")]
    linked = [item for item in concrete if shot_id in {str(value) for value in item.get("shot_ids", [])}]
    pool = linked or concrete
    if desired_media_type:
        typed = [item for item in pool if str(item.get("kind")) == desired_media_type]
        if typed:
            pool = typed
    pool = [item for item in pool if float(item.get("duplicate_confidence", 0) or 0) < 0.85]
    pool.sort(key=lambda item: float(item.get("relevance_score", 0) or 0), reverse=True)
    generated = [item for item in candidates if item.get("generated")]
    return [*pool, *generated]
