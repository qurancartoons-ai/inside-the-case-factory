from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.core.progress import TaskQueue


@dataclass(frozen=True)
class ResearchPage:
    kind: str
    page: int
    page_size: int
    total: int
    items: list[dict[str, Any]]
    cache_hit: bool

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "page": self.page, "page_size": self.page_size, "total": self.total, "items": self.items, "cache_hit": self.cache_hit}


class ResearchPanelService:
    """Read-once, mtime-invalidated projections for the lazy Research Panel."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.cache_dir = project_root / "workspace" / "research_panel_cache"

    def _manifest(self, kind: str) -> tuple[Path, str]:
        if kind == "sources":
            return self.project_root / "manifests" / "sources.json", "sources"
        if kind == "claims":
            return self.project_root / "manifests" / "claims.json", "claims"
        raise ValueError("Research kind must be sources or claims")

    @staticmethod
    def _signature(path: Path) -> str:
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def page(self, kind: str, page: int = 1, page_size: int = 25) -> ResearchPage:
        path, key = self._manifest(kind)
        page = max(1, page); page_size = max(5, min(100, page_size))
        signature = self._signature(path)
        cache_key = hashlib.sha256(f"{kind}:{page}:{page_size}".encode()).hexdigest()[:16]
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            cached = read_json(cache_path)
            if cached.get("signature") == signature:
                return ResearchPage(kind, page, page_size, int(cached["total"]), list(cached["items"]), True)
        manifest = read_json(path)  # exactly one manifest read on cache miss
        raw = manifest.get(key, []) if isinstance(manifest, dict) else []
        start = (page - 1) * page_size
        selected = raw[start:start + page_size]
        if kind == "sources":
            items = [{
                "id": item.get("id"), "title": item.get("title"), "url": item.get("url"),
                "publisher": item.get("publisher"), "review_status": item.get("review_status"),
                "transcript_preview": self._transcript_preview(item), "has_transcript": bool(item.get("transcript")),
                "attachments": [{"url": attachment.get("url", attachment.get("path", "")), "title": attachment.get("title", "Bijlage")} for attachment in item.get("attachments", [])[:3] if isinstance(attachment, dict)],
            } for item in selected]
        else:
            items = [{
                "id": item.get("id"), "text": item.get("text"), "source_ids": item.get("source_ids", []),
                "review_status": item.get("review_status"), "confidence": item.get("confidence"),
            } for item in selected]
        payload = {"signature": signature, "total": len(raw), "items": items}
        write_json(cache_path, payload)
        return ResearchPage(kind, page, page_size, len(raw), items, False)

    @staticmethod
    def _transcript_preview(source: dict[str, Any], limit: int = 240) -> str:
        transcript = source.get("transcript", "")
        if isinstance(transcript, list):
            text = " ".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in transcript[:3])
        else:
            text = str(transcript)
        return text[:limit] + ("…" if len(text) > limit else "")

    def transcript(self, source_id: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        path, _ = self._manifest("sources")
        manifest = read_json(path)  # transcript is read only after explicit request
        source = next((item for item in manifest.get("sources", []) if str(item.get("id")) == source_id), None)
        if source is None:
            raise KeyError(source_id)
        transcript = source.get("transcript", "")
        text = "\n".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in transcript) if isinstance(transcript, list) else str(transcript)
        offset = max(0, offset); limit = max(200, min(5000, limit))
        return {"source_id": source_id, "offset": offset, "limit": limit, "total_characters": len(text), "text": text[offset:offset + limit], "has_more": offset + limit < len(text)}

    def queue_analysis(self, instruction: str) -> dict[str, Any]:
        path = self.project_root / "manifests" / "research_analysis_queue.json"
        queue = read_json(path) if path.exists() else {"version": 1, "jobs": []}
        existing = next((job for job in queue["jobs"] if job.get("instruction") == instruction and job.get("status") in {"queued", "running"}), None)
        if existing:
            return existing
        job = {"id": f"research-job-{len(queue['jobs']) + 1}", "instruction": instruction, "status": "queued", "provider_calls": 0}
        queue["jobs"].append(job); write_json(path, queue)
        TaskQueue(self.project_root).enqueue("research", instruction, heavy=True)
        return job
