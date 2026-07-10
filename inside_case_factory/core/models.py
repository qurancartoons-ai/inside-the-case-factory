from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SourceRecord:
    title: str
    url: str | None
    publisher: str | None
    retrieved_at: str
    notes: str = ""


@dataclass(frozen=True)
class ProductionProject:
    slug: str
    topic: str
    root: Path
    status: ReviewStatus = ReviewStatus.DRAFT
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifests" / "project.json"
