"""Shared presentation contract for Hub-owned workflow-runtime operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OPERATIONS_LIST_SCHEMA = "ananta.workflow_runtime_operations_list.v1"
OPERATIONS_RECORD_SCHEMA = "ananta.workflow_runtime_operations_record.v1"


class WorkflowRuntimeProjectionError(ValueError):
    """Raised when a client receives a non-canonical Hub projection."""


def require_operations_list(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the stable list envelope without recreating Hub evaluation."""

    if payload.get("schema") != OPERATIONS_LIST_SCHEMA:
        raise WorkflowRuntimeProjectionError(_reason_code(payload))
    runs = payload.get("runs")
    summary = payload.get("summary")
    if not isinstance(runs, list) or not isinstance(summary, Mapping):
        raise WorkflowRuntimeProjectionError("workflow_runtime_operations_projection_invalid")
    if any(not isinstance(run, Mapping) or run.get("schema") != OPERATIONS_RECORD_SCHEMA for run in runs):
        raise WorkflowRuntimeProjectionError("workflow_runtime_operations_projection_invalid")
    return dict(payload)


def require_operation_detail(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a detail envelope returned by the same Hub projection API."""

    run = payload.get("run")
    if payload.get("status") != "ok" or not isinstance(run, Mapping):
        raise WorkflowRuntimeProjectionError(_reason_code(payload))
    if run.get("schema") != OPERATIONS_RECORD_SCHEMA:
        raise WorkflowRuntimeProjectionError("workflow_runtime_operations_projection_invalid")
    return dict(run)


def operations_status_line(payload: Mapping[str, Any]) -> str:
    """Render only evaluated facts supplied by the Hub; never infer success."""

    validated = require_operations_list(payload)
    summary = validated["summary"]
    return (
        f"runs={int(summary.get('total_runs') or 0)} "
        f"degraded={int(summary.get('degraded_runs') or 0)} "
        f"stale={int(summary.get('stale_runs') or 0)} "
        f"unverified={int(summary.get('unverified_successes') or 0)} "
        f"open_gates={int(summary.get('open_gates') or 0)}"
    )


def _reason_code(payload: Mapping[str, Any]) -> str:
    return str(payload.get("reason_code") or payload.get("message") or "workflow_runtime_operations_unavailable")


__all__ = [
    "OPERATIONS_LIST_SCHEMA",
    "OPERATIONS_RECORD_SCHEMA",
    "WorkflowRuntimeProjectionError",
    "operations_status_line",
    "require_operation_detail",
    "require_operations_list",
]
