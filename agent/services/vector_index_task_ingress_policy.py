from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON = "vector_index_reserved_task_ingress_forbidden"
RESERVED_VECTOR_INDEX_TASK_KIND = "vector_index_operation"
RESERVED_VECTOR_INDEX_TASK_SOURCE = "vector_index"
RESERVED_VECTOR_INDEX_CONTEXT_KEY = "vector_index_task"


def find_reserved_vector_index_marker(
    payload: Mapping[str, Any] | None,
    *,
    source: Any = None,
) -> str | None:
    """Return the first Vector-Index-only marker found at a generic task boundary."""

    values = payload if isinstance(payload, Mapping) else {}
    source_candidates = (source, values.get("source"))
    if any(
        str(candidate or "").strip().lower() == RESERVED_VECTOR_INDEX_TASK_SOURCE for candidate in source_candidates
    ):
        return "source"
    history = values.get("history")
    if isinstance(history, list):
        for event in history:
            if not isinstance(event, Mapping) or event.get("event_type") != "task_ingested":
                continue
            details = event.get("details")
            if (
                isinstance(details, Mapping)
                and str(details.get("source") or "").strip().lower() == RESERVED_VECTOR_INDEX_TASK_SOURCE
            ):
                return "source"
    if str(values.get("task_kind") or "").strip().lower() == RESERVED_VECTOR_INDEX_TASK_KIND:
        return "task_kind"
    worker_context = values.get("worker_execution_context")
    if isinstance(worker_context, Mapping) and RESERVED_VECTOR_INDEX_CONTEXT_KEY in worker_context:
        return f"worker_execution_context.{RESERVED_VECTOR_INDEX_CONTEXT_KEY}"
    return None


def reserved_vector_index_ingress_error(marker: str) -> dict[str, Any]:
    """Build the stable fail-closed result shared by generic task boundaries."""

    return {
        "error": RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
            "reserved_field": marker,
        },
    }


__all__ = [
    "RESERVED_VECTOR_INDEX_CONTEXT_KEY",
    "RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON",
    "RESERVED_VECTOR_INDEX_TASK_KIND",
    "RESERVED_VECTOR_INDEX_TASK_SOURCE",
    "find_reserved_vector_index_marker",
    "reserved_vector_index_ingress_error",
]
