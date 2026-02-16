from __future__ import annotations


_STATUS_ALIASES = {
    "to-do": "todo",
    "backlog": "todo",
    "in-progress": "in_progress",
    "in progress": "in_progress",
    "done": "completed",
    "complete": "completed",
}


_CANONICAL_QUERY_VALUES = {
    "todo": ["todo", "to-do", "backlog"],
    "in_progress": ["in_progress", "in-progress", "in progress"],
    "completed": ["completed", "done", "complete"],
}


def normalize_task_status(status: str | None, default: str = "todo") -> str:
    raw = (status or "").strip().lower()
    if not raw:
        return default
    return _STATUS_ALIASES.get(raw, raw.replace("-", "_").replace(" ", "_"))


def expand_task_status_query_values(status: str | None) -> list[str]:
    canonical = normalize_task_status(status, default="")
    if not canonical:
        return []
    values = _CANONICAL_QUERY_VALUES.get(canonical, [canonical])
    # Dedupe while preserving order
    return list(dict.fromkeys(values))
