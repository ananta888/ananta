"""Request-local boundary for Hub-owned recovery Task results.

Workers may compute recovery proposals, verification data, and execution
results.  Only the Hub may publish those values to the authoritative Task
record after it has revalidated the matching dispatch lease.  A ContextVar
keeps that rule effective in shared-database deployments without introducing
process-global state.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class DeferredRecoveryTaskWrites:
    """Diagnostic record of Task mutations suppressed during one invocation."""

    task_id: str
    phase: str
    mutations: list[dict[str, Any]] = field(default_factory=list)


_ACTIVE_BOUNDARY: ContextVar[DeferredRecoveryTaskWrites | None] = ContextVar(
    "ananta_recovery_result_write_boundary",
    default=None,
)
RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS = frozenset(
    {
        "source_catalog",
        "answer_verification",
        "task_flow_metrics",
        "llm_diagnostics",
        "execution_scope",
        "execution_provenance",
        "loop_telemetry",
        "artifact_snapshot_diff",
        "workspace_state_sync",
        "execution_routing",
    }
)


def _verification_projection(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: copy.deepcopy(value[key])
        for key in sorted(
            RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS
        )
        if key in value
    }


def _matching_boundary(
    task_id: str,
) -> DeferredRecoveryTaskWrites | None:
    boundary = _ACTIVE_BOUNDARY.get()
    if (
        boundary is None
        or boundary.task_id != str(task_id or "").strip()
    ):
        return None
    return boundary


def _record(
    task_id: str,
    *,
    operation: str,
    details: dict[str, Any] | None = None,
) -> bool:
    boundary = _matching_boundary(task_id)
    if boundary is None:
        return False
    boundary.mutations.append(
        {
            "operation": str(operation or "").strip(),
            **dict(details or {}),
        }
    )
    return True


@contextmanager
def defer_recovery_task_writes(
    *,
    task_id: str,
    phase: str,
) -> Iterator[DeferredRecoveryTaskWrites]:
    """Prevent this execution request from publishing its own Task result."""

    boundary = DeferredRecoveryTaskWrites(
        task_id=str(task_id or "").strip(),
        phase=str(phase or "").strip().lower(),
    )
    token = _ACTIVE_BOUNDARY.set(boundary)
    try:
        yield boundary
    finally:
        _ACTIVE_BOUNDARY.reset(token)


def defer_task_status_mutation(
    task_id: str,
    status: str,
    *,
    event_type: str | None,
    event_actor: str,
    event_details: dict[str, Any] | None,
    force: bool,
    values: dict[str, Any],
) -> bool:
    """Record and suppress a matching status helper mutation."""

    projection = _verification_projection(
        values.get("verification_status")
    )
    return _record(
        task_id,
        operation="status_update",
        details={
            "status": str(status or ""),
            "event_type": str(event_type or "") or None,
            "event_actor": str(event_actor or ""),
            "event_details": dict(event_details or {}),
            "force": bool(force),
            "value_keys": sorted(str(key) for key in values),
            **(
                {"verification_projection": projection}
                if projection
                else {}
            ),
        },
    )


def defer_task_compare_and_set(
    task_id: str,
    status: str,
    *,
    expected_statuses: set[str] | list[str] | tuple[str, ...],
    values: dict[str, Any],
) -> bool:
    """Record and suppress a matching compare-and-set mutation."""

    projection = _verification_projection(
        values.get("verification_status")
    )
    return _record(
        task_id,
        operation="compare_and_set",
        details={
            "status": str(status or ""),
            "expected_statuses": sorted(
                str(value or "") for value in expected_statuses
            ),
            "value_keys": sorted(str(key) for key in values),
            **(
                {"verification_projection": projection}
                if projection
                else {}
            ),
        },
    )


def defer_task_repository_save(
    task_id: str,
    *,
    task: Any | None = None,
) -> bool:
    """Record and suppress a matching direct Task repository save."""

    projection = _verification_projection(
        getattr(task, "verification_status", None)
    )
    return _record(
        task_id,
        operation="repository_save",
        details=(
            {"verification_projection": projection}
            if projection
            else None
        ),
    )


def defer_task_verification_mutation(
    task_id: str,
    *,
    trace_id: str | None,
) -> bool:
    """Record and suppress a matching verification-record publication."""

    return _record(
        task_id,
        operation="verification_record",
        details={"trace_id": str(trace_id or "") or None},
    )


__all__ = [
    "DeferredRecoveryTaskWrites",
    "RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS",
    "defer_recovery_task_writes",
    "defer_task_compare_and_set",
    "defer_task_repository_save",
    "defer_task_status_mutation",
    "defer_task_verification_mutation",
]
