from __future__ import annotations


_STATUS_ALIASES = {
    "to-do": "todo",
    "backlog": "todo",
    "in-progress": "in_progress",
    "in progress": "in_progress",
    "done": "completed",
    "complete": "completed",
}


def normalize_task_status(status: str | None, default: str = "todo") -> str:
    raw = (status or "").strip().lower()
    if not raw:
        return default
    return _STATUS_ALIASES.get(raw, raw.replace("-", "_").replace(" ", "_"))
