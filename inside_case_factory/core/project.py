from __future__ import annotations

import json
from pathlib import Path
import re

from inside_case_factory.core.models import ProductionProject, ReviewStatus
from inside_case_factory.utils.files import write_json


PROJECT_SUBDIRS = [
    "assets/images",
    "assets/clips",
    "assets/audio",
    "exports",
    "manifests",
    "research",
    "review",
    "workspace",
]


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return normalized.strip("-") or "untitled-case"


def available_project_slug(projects_dir: Path, topic: str) -> str:
    """Return a stable, unused slug without ever mixing two project dossiers."""
    base = slugify(topic)
    candidate = base
    suffix = 2
    while (projects_dir / candidate / "manifests" / "project.json").exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_project(projects_dir: Path, topic: str, slug: str | None = None) -> ProductionProject:
    project_slug = slug or slugify(topic)
    root = projects_dir / project_slug
    for subdir in PROJECT_SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)

    project = ProductionProject(
        slug=project_slug,
        topic=topic,
        root=root,
        status=ReviewStatus.DRAFT,
    )
    write_project_manifest(project)
    write_media_manifest_if_missing(project)
    write_research_manifests_if_missing(project)
    return project


def write_project_manifest(project: ProductionProject) -> None:
    payload = {
        "slug": project.slug,
        "topic": project.topic,
        "status": project.status.value,
        "created_at": project.created_at,
        "workflow_type": "create_documentary",
        "paths": {
            "research": "research",
            "manifests": "manifests",
            "review": "review",
            "workspace": "workspace",
            "assets": "assets",
            "exports": "exports",
        },
    }
    project.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    project.manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_media_manifest_if_missing(project: ProductionProject) -> None:
    path = project.root / "manifests" / "media_sources.json"
    if path.exists():
        return
    write_json(
        path,
        {
            "version": 1,
            "description": (
                "Manually curated media sources for this project. Add licensed or owned images "
                "here before generation to have the render pipeline use them for mapped scenes."
            ),
            "assets": [],
        },
    )


def write_research_manifests_if_missing(project: ProductionProject) -> None:
    manifests = {
        "sources.json": {"version": 1, "sources": []},
        "research.json": {
            "version": 1,
            "provider": "manual",
            "status": "not_started",
            "notes": "",
            "findings": [],
        },
        "timeline.json": {"version": 1, "events": []},
        "claims.json": {"version": 1, "claims": []},
        "workflow.json": {
            "version": 1,
            "workflow_type": "create_documentary",
            "stage": "research",
            "target_duration_minutes": 10,
            "research_approved": False,
            "script_approved": False,
            "scenes_generated": False,
            "voiceover_generated": False,
            "video_rendered": False,
            "reference_documentary_loaded": False,
            "recycle_analysis_ready": False,
            "recycle_verification_ready": False,
            "recycle_reconstruction_ready": False,
        },
    }
    for filename, payload in manifests.items():
        path = project.root / "manifests" / filename
        if not path.exists():
            write_json(path, payload)
