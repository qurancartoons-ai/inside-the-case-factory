from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from inside_case_factory.utils.files import read_json, write_json


HEAVY_TASKS = {"research", "script", "media_analysis", "voice_over", "render"}
EVENT_TYPES = {"started", "source_found", "source_processed", "claim_created", "waiting_for_provider", "retrying", "completed", "blocked", "failed"}


def _now() -> datetime:
    return datetime.now(UTC)


def _manifest(project_root: Path, name: str, fallback: dict[str, Any]) -> dict[str, Any]:
    path = project_root / "manifests" / name
    return read_json(path) if path.exists() else fallback


def write_progress_event(project_root: Path, event: str, stage: str, message: str, **details: Any) -> dict[str, Any]:
    if event not in EVENT_TYPES:
        raise ValueError(f"Unsupported progress event: {event}")
    path = project_root / "manifests" / "progress_events.json"
    data = _manifest(project_root, "progress_events.json", {"version": 1, "events": []})
    entry = {"id": len(data["events"]) + 1, "at": _now().isoformat(), "event": event, "stage": stage, "message": message, **details}
    data["events"].append(entry)
    data["events"] = data["events"][-500:]
    write_json(path, data)
    return entry


class TaskQueue:
    """Persistent queue: one resource-heavy task, independent light work may overlap."""

    def __init__(self, project_root: Path, stall_after_seconds: int = 300) -> None:
        self.project_root = project_root
        self.path = project_root / "manifests" / "task_queue.json"
        self.stall_after_seconds = max(30, stall_after_seconds)

    def _load(self) -> dict[str, Any]:
        return _manifest(self.project_root, "task_queue.json", {"version": 1, "tasks": []})

    def _save(self, data: dict[str, Any]) -> None:
        write_json(self.path, data)

    def enqueue(self, kind: str, label: str, *, heavy: bool | None = None) -> dict[str, Any]:
        data = self._load(); tasks = data.setdefault("tasks", [])
        heavy = kind in HEAVY_TASKS if heavy is None else heavy
        active_heavy = any(item.get("status") == "active" and item.get("heavy") for item in tasks)
        status = "waiting" if heavy and active_heavy else "active"
        now = _now().isoformat()
        task = {"id": f"task-{len(tasks)+1}", "kind": kind, "label": label, "heavy": heavy, "status": status, "created_at": now, "started_at": now if status == "active" else "", "updated_at": now, "retries": 0, "progress": 0, "status_writes": 1}
        tasks.append(task); self._save(data)
        write_progress_event(self.project_root, "started" if status == "active" else "blocked", kind, label, task_id=task["id"], queue_status=status)
        return task

    def update(self, task_id: str, status: str, *, progress: int | None = None, reason: str = "") -> dict[str, Any]:
        data = self._load(); task = next(item for item in data.get("tasks", []) if item.get("id") == task_id)
        task["status_writes"] = int(task.get("status_writes", 0)) + (1 if task.get("status") == status else 0)
        task.update({"status": status, "updated_at": _now().isoformat()})
        if progress is not None: task["progress"] = max(0, min(100, progress))
        if reason: task["reason"] = reason
        if status in {"completed", "failed", "stopped", "blocked"}: task["finished_at"] = task["updated_at"]
        self._promote(data); self._save(data)
        event = {"active": "started", "completed": "completed", "blocked": "blocked", "failed": "failed"}.get(status)
        if event: write_progress_event(self.project_root, event, str(task.get("kind", "")), reason or str(task.get("label", "")), task_id=task_id)
        return task

    def _promote(self, data: dict[str, Any]) -> None:
        if any(item.get("status") == "active" and item.get("heavy") for item in data.get("tasks", [])): return
        waiting = next((item for item in data.get("tasks", []) if item.get("status") == "waiting" and item.get("heavy")), None)
        if waiting:
            waiting.update({"status": "active", "started_at": _now().isoformat(), "updated_at": _now().isoformat()})

    def action(self, task_id: str, action: str) -> dict[str, Any]:
        data = self._load(); task = next(item for item in data.get("tasks", []) if item.get("id") == task_id)
        if action == "stop":
            task.update({"status": "stopped", "finished_at": _now().isoformat(), "updated_at": _now().isoformat(), "reason": "Gestopt door gebruiker"})
        elif action in {"resume", "retry"}:
            if action == "retry": task["retries"] = int(task.get("retries", 0)) + 1
            active_heavy = any(item is not task and item.get("status") == "active" and item.get("heavy") for item in data.get("tasks", []))
            task.update({"status": "waiting" if task.get("heavy") and active_heavy else "active", "updated_at": _now().isoformat(), "reason": ""})
            if task["status"] == "active": task["started_at"] = task.get("started_at") or task["updated_at"]
            write_progress_event(self.project_root, "retrying" if action == "retry" else "started", str(task.get("kind", "")), "Taak opnieuw gestart", task_id=task_id)
        else: raise ValueError("Unknown task action")
        self._promote(data); self._save(data); return task

    def snapshot(self) -> dict[str, Any]:
        data = self._load(); now = _now(); changed = False
        artifact_latest = max((p.stat().st_mtime for p in self.project_root.rglob("*") if p.is_file() and "task_queue.json" not in str(p) and "progress_events.json" not in str(p)), default=0)
        for task in data.get("tasks", []):
            started_at = task.get("started_at")
            if started_at:
                start = datetime.fromisoformat(str(started_at)).astimezone(UTC)
                end_value = task.get("finished_at")
                end = datetime.fromisoformat(str(end_value)).astimezone(UTC) if end_value else now
                task["duration_seconds"] = max(0, int((end - start).total_seconds()))
            if task.get("status") != "active": continue
            updated = datetime.fromisoformat(str(task.get("updated_at"))).astimezone(UTC)
            no_progress = now - updated > timedelta(seconds=self.stall_after_seconds)
            no_artifacts = artifact_latest <= updated.timestamp()
            repeated = int(task.get("status_writes", 0)) >= 5
            if no_progress and no_artifacts or repeated:
                task.update({"status": "possibly_stalled", "reason": "Geen nieuwe voortgang of gewijzigde resultaten" if no_progress else "Dezelfde status werd herhaald", "stalled_at": now.isoformat()}); changed = True
        if changed: self._save(data)
        tasks = data.get("tasks", [])
        return {"tasks": tasks, "active": [t for t in tasks if t.get("status") == "active"], "waiting": [t for t in tasks if t.get("status") == "waiting"], "completed": [t for t in tasks if t.get("status") == "completed"], "blocked": [t for t in tasks if t.get("status") in {"blocked", "failed", "possibly_stalled"}]}
