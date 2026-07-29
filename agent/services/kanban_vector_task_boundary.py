"""Fail-closed Vector-domain boundary for generic Kanban mutations."""

from __future__ import annotations

from typing import Any

from agent.services.kanban_service_error import KanbanServiceError
from agent.services.vector_store_authorization_policy import (
    has_reserved_vector_index_marker,
)
from agent.services.vector_task_admin_guard_service import (
    generic_vector_mutation_error,
)


def is_kanban_rankable_task(task: Any) -> bool:
    """Exclude reserved tasks from indirect rank/revision mutations."""

    return not has_reserved_vector_index_marker(task)


def require_kanban_mutation_allowed(task: Any) -> None:
    """Reject complete and partial Vector markers at the generic boundary."""

    result = generic_vector_mutation_error(task)
    if result is None:
        return
    raise KanbanServiceError(
        result["error"],
        "Vector index tasks require the dedicated control plane",
        status_code=result["code"],
        details=result["data"],
    )


__all__ = [
    "is_kanban_rankable_task",
    "require_kanban_mutation_allowed",
]
