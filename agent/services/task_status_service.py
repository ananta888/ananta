from __future__ import annotations

from agent.models import TaskStatus

_STATUS_ALIASES = {
    "to-do": TaskStatus.TODO.value,
    "backlog": TaskStatus.TODO.value,
    "in-progress": TaskStatus.IN_PROGRESS.value,
    "in progress": TaskStatus.IN_PROGRESS.value,
    "done": TaskStatus.COMPLETED.value,
    "complete": TaskStatus.COMPLETED.value,
    "canceled": TaskStatus.CANCELLED.value,
    "blocked": TaskStatus.BLOCKED_BY_DEPENDENCY.value,
}

_CANONICAL_QUERY_VALUES = {
    TaskStatus.TODO.value: ["todo", "to-do", "backlog"],
    TaskStatus.IN_PROGRESS.value: ["in_progress", "in-progress", "in progress"],
    TaskStatus.COMPLETED.value: ["completed", "done", "complete"],
    TaskStatus.CANCELLED.value: ["cancelled", "canceled"],
    TaskStatus.PAUSED.value: ["paused"],
    TaskStatus.BLOCKED_BY_DEPENDENCY.value: ["blocked_by_dependency", "blocked"],
    TaskStatus.WAITING_FOR_REVIEW.value: ["waiting_for_review"],
    TaskStatus.VERIFICATION_FAILED.value: ["verification_failed"],
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
    return list(dict.fromkeys(values))
