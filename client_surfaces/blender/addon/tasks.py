from __future__ import annotations


def normalize_tasks(items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(items or []):
        payload = dict(item or {})
        payload.setdefault("id", payload.get("task_id") or payload.get("goal_id") or "")
        payload.setdefault("status", "unknown")
        payload.setdefault("title", payload.get("summary") or payload.get("goal") or payload.get("id") or "Untitled task")
        normalized.append(payload)
    return normalized


def update_task_cache(previous: list[dict] | None, incoming: list[dict] | None, *, keep_previous_on_error: bool = True) -> list[dict]:
    if incoming is None and keep_previous_on_error:
        return normalize_tasks(previous)
    return normalize_tasks(incoming)
