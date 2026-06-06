"""SCTR-005/SCTR-006: read-only Todo track tool for SnakeChat."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TodoReadResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


class TodoReadTool:
    """Read todo.track JSON files from the repository todos directory."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(str(workspace_root or "")).resolve()
        self._todos_dir = (self._root / "todos").resolve()

    def _todo_files(self) -> list[Path]:
        if not self._todos_dir.exists() or not self._todos_dir.is_dir():
            return []
        return sorted(self._todos_dir.glob("todo.*.json"))

    def _safe_todo_path(self, relative_path: str) -> Path | None:
        candidate = (self._root / relative_path).resolve()
        if not candidate.is_relative_to(self._todos_dir):
            return None
        if candidate.name == "todo.track.schema.json":
            return None
        return candidate

    @staticmethod
    def _load(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _track_summary(path: Path, data: dict[str, Any]) -> dict[str, Any]:
        tasks = [t for t in list(data.get("tasks") or []) if isinstance(t, dict)]
        status_counts = Counter(str(t.get("status") or "todo") for t in tasks)
        return {
            "file": str(path.name),
            "track": str(data.get("track") or path.stem),
            "total": len(tasks),
            "done": status_counts.get("done", 0),
            "todo": status_counts.get("todo", 0),
            "in_progress": status_counts.get("in_progress", 0),
            "blocked": status_counts.get("blocked", 0),
        }

    def list_active_tracks(self) -> TodoReadResult:
        tracks = []
        for path in self._todo_files():
            if path.name == "todo.track.schema.json":
                continue
            data = self._load(path)
            if data is None:
                continue
            summary = self._track_summary(path, data)
            if summary["todo"] or summary["in_progress"] or summary["blocked"]:
                tracks.append(summary)
        return TodoReadResult(ok=True, data={"tracks": tracks, "count": len(tracks)})

    def list_todos(self, relative_path: str, *, limit: int = 50) -> TodoReadResult:
        path = self._safe_todo_path(relative_path)
        if path is None:
            return TodoReadResult(ok=False, error="todo_path_denied")
        data = self._load(path)
        if data is None:
            return TodoReadResult(ok=False, error=f"todo_not_readable:{relative_path!r}")
        safe_limit = max(1, min(int(limit or 50), 200))
        tasks = [
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": str(task.get("status") or "todo"),
                "priority": str(task.get("priority") or ""),
                "risk": str(task.get("risk") or ""),
            }
            for task in list(data.get("tasks") or [])[:safe_limit]
            if isinstance(task, dict)
        ]
        return TodoReadResult(
            ok=True,
            data={
                "file": path.name,
                "track": str(data.get("track") or path.stem),
                "tasks": tasks,
                "count": len(tasks),
            },
        )

    def find_task_by_id(self, task_id: str) -> TodoReadResult:
        needle = str(task_id or "").strip()
        if not needle:
            return TodoReadResult(ok=False, error="task_id_required")
        for path in self._todo_files():
            data = self._load(path)
            if data is None:
                continue
            for task in list(data.get("tasks") or []):
                if isinstance(task, dict) and str(task.get("id") or "") == needle:
                    return TodoReadResult(
                        ok=True,
                        data={
                            "file": path.name,
                            "track": str(data.get("track") or path.stem),
                            "task": task,
                        },
                    )
        return TodoReadResult(ok=False, error=f"task_not_found:{needle}")
