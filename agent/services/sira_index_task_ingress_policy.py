"""Protect Hub-created SIRA operation tasks from generic task mutation APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.sira_index_operation import CONTEXT_KEY, SCHEMA, TASK_KIND

RESERVED_SIRA_TASK_INGRESS_REASON = "sira_index_reserved_task_ingress_forbidden"
BOUND_SIRA_TASK_MUTATION_REASON = "sira_index_task_control_plane_mutation_forbidden"


def _payload(task: Any) -> Mapping[str, Any]:
    if isinstance(task, Mapping):
        return task
    dump = getattr(task, "model_dump", None)
    return dump() if callable(dump) else {}


def find_reserved_sira_index_marker(
    payload: Mapping[str, Any] | None,
    *,
    source: Any = None,
) -> str | None:
    values = payload if isinstance(payload, Mapping) else {}
    if any(str(candidate or "").strip().lower() == "codecompass_sira" for candidate in (source, values.get("source"))):
        return "source"
    if str(values.get("task_kind") or "").strip().lower() == TASK_KIND:
        return "task_kind"
    context = values.get("worker_execution_context")
    if isinstance(context, Mapping) and CONTEXT_KEY in context:
        return f"worker_execution_context.{CONTEXT_KEY}"
    return None


def has_bound_sira_index_operation(task: Any) -> bool:
    context = _payload(task).get("worker_execution_context")
    operation = context.get(CONTEXT_KEY) if isinstance(context, Mapping) else None
    return bool(isinstance(operation, Mapping) and operation.get("schema") == SCHEMA)


def reserved_sira_index_ingress_error(marker: str) -> dict[str, Any]:
    return {
        "error": RESERVED_SIRA_TASK_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_SIRA_TASK_INGRESS_REASON,
            "reserved_field": marker,
        },
    }


def bound_sira_index_mutation_error(
    task: Any,
    *,
    action: str,
) -> dict[str, Any] | None:
    if not has_bound_sira_index_operation(task):
        return None
    return {
        "error": BOUND_SIRA_TASK_MUTATION_REASON,
        "code": 409,
        "data": {
            "reason_code": BOUND_SIRA_TASK_MUTATION_REASON,
            "task_id": str(_payload(task).get("id") or ""),
            "action": str(action or "").strip(),
        },
    }


__all__ = [
    "bound_sira_index_mutation_error",
    "find_reserved_sira_index_marker",
    "has_bound_sira_index_operation",
    "reserved_sira_index_ingress_error",
]
