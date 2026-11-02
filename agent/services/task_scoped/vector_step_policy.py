"""Fail-closed policy responses for reserved Vector task steps."""

from __future__ import annotations

from typing import Any

from agent.services._vector_index_result_forwarding import (
    is_authoritative_vector_index_task,
)


def vector_index_domain_binding_error(
    task: dict[str, Any],
):
    """Reject a Task when exactly one reserved Vector marker is present."""

    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    try:
        is_authoritative_vector_index_task(task)
    except ValueError as exc:
        reason = str(exc)
        return TaskScopedRouteResponse(
            data={
                "status": "denied",
                "reason_code": reason,
                "task_id": str(task.get("id") or ""),
            },
            status="denied",
            message="Vector index task domain binding is invalid",
            code=409,
        )
    return None


def vector_index_handler_unavailable(
    *,
    task: dict[str, Any],
    phase: str,
):
    """Keep reserved Vector tasks out of every generic execution path."""

    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "degraded",
            "reason_code": "vector_index_worker_handler_unavailable",
            "task_id": str(task.get("id") or ""),
            "phase": phase,
        },
        status="degraded",
        message="Vector index worker handler is unavailable",
        code=503,
    )
