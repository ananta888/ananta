"""Protect Hub-owned retrieval-vector bindings at generic task APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.retrieval_vector_scope_binding_service import (
    RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY,
)

RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON = "retrieval_vector_scope_reserved_ingress_forbidden"


def find_reserved_retrieval_vector_scope_marker(
    payload: Mapping[str, Any] | None,
) -> str | None:
    values = payload if isinstance(payload, Mapping) else {}
    worker_context = values.get("worker_execution_context")
    if isinstance(worker_context, Mapping) and RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY in worker_context:
        return f"worker_execution_context.{RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY}"
    return None


def reserved_retrieval_vector_scope_ingress_error(
    marker: str,
) -> dict[str, Any]:
    return {
        "error": RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON,
            "reserved_field": marker,
        },
    }


def preserve_hub_retrieval_vector_scope(
    *,
    existing_task: Mapping[str, Any],
    update_data: dict[str, Any],
) -> None:
    """Keep the reserved block when an external patch replaces other context."""

    if "worker_execution_context" not in update_data:
        return
    existing_context = existing_task.get("worker_execution_context")
    incoming_context = update_data.get("worker_execution_context")
    if not isinstance(existing_context, Mapping):
        return
    reserved = existing_context.get(RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY)
    if reserved is None:
        return
    normalized_incoming = dict(incoming_context) if isinstance(incoming_context, Mapping) else {}
    normalized_incoming[RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY] = reserved
    update_data["worker_execution_context"] = normalized_incoming


__all__ = [
    "RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON",
    "find_reserved_retrieval_vector_scope_marker",
    "preserve_hub_retrieval_vector_scope",
    "reserved_retrieval_vector_scope_ingress_error",
]
