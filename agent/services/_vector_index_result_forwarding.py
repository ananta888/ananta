"""Hub-side boundary for forwarded vector-index Worker results.

The generic task-forwarding service delegates the vector-index result contract
to this module.  Workers only return bounded execution results; the Hub-owned
vector-index task service remains responsible for validating and persisting
them atomically.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

_VECTOR_INDEX_RESULT_SCHEMA = "ananta.vector_index_task_result.v1"
_VECTOR_INDEX_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "attempt_id",
        "idempotency_key",
        "operation",
        "status",
        "reason_code",
        "diagnostics",
        "result",
        "error",
    }
)
_FORWARDED_HANDLER_FRAMEWORK_FIELDS = frozenset({"handler_contract"})


def is_authoritative_vector_index_task(
    task: Mapping[str, Any],
) -> bool:
    """Classify the domain from Hub-owned task fields, never Worker output."""

    task_kind = str(task.get("task_kind") or "").strip().lower()
    context = task.get("worker_execution_context")
    envelope = (
        context.get("vector_index_task")
        if isinstance(context, Mapping)
        else None
    )
    has_kind = task_kind == "vector_index_operation"
    has_envelope = (
        isinstance(envelope, Mapping)
        and envelope.get("schema") == "ananta.vector_index_task.v1"
    )
    if has_kind != has_envelope:
        raise ValueError("vector_index_task_domain_binding_invalid")
    return has_kind and has_envelope


def vector_index_result_candidate(
    response: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the bounded domain result when ``response`` uses its schema."""

    if str(response.get("schema") or "") != _VECTOR_INDEX_RESULT_SCHEMA:
        return None
    unknown_fields = set(response) - _VECTOR_INDEX_RESULT_FIELDS - _FORWARDED_HANDLER_FRAMEWORK_FIELDS
    if unknown_fields:
        raise ValueError("vector_index_result_forwarding_fields_unknown")
    return {field: response.get(field) for field in _VECTOR_INDEX_RESULT_FIELDS}


def accept_forwarded_vector_index_result(
    *,
    job_id: str,
    result: Mapping[str, Any] | None,
    status_values: Mapping[str, Any],
    recovery_child: bool,
) -> bool:
    """Persist a vector-index result through its Hub-owned task service.

    ``False`` tells the caller that the response belongs to another task
    domain and must use the generic status path.
    """

    if result is None:
        return False
    if recovery_child:
        raise ValueError("vector_index_result_recovery_task_forbidden")

    from agent.services.vector_index_task_service import (
        get_vector_index_task_service,
    )

    get_vector_index_task_service().accept_worker_result(
        job_id=job_id,
        result=result,
        status_values=status_values,
    )
    return True


def accept_bound_forwarded_vector_index_result(
    *,
    job_id: str,
    response: Mapping[str, Any],
    task: Mapping[str, Any],
    load_task: Callable[[str], Any],
    classify_task: Callable[[Mapping[str, Any]], bool],
    extract_result: Callable[[Mapping[str, Any]], dict[str, Any] | None],
    accept_result: Callable[..., bool],
) -> bool:
    """Validate Hub-owned task/result binding before a Vector result commit."""

    authoritative_task = task
    persisted_task = load_task(job_id)
    if persisted_task is not None:
        authoritative_task = (
            dict(persisted_task.model_dump())
            if callable(getattr(persisted_task, "model_dump", None))
            else dict(persisted_task)
        )
    if str(authoritative_task.get("id") or job_id) != str(job_id):
        raise ValueError("forwarded_result_task_binding_invalid")
    authoritative_vector_task = classify_task(authoritative_task)
    vector_index_result = extract_result(response)
    if authoritative_vector_task:
        if vector_index_result is None:
            raise ValueError("vector_index_result_schema_required")
        if not accept_result(
            job_id=job_id,
            result=vector_index_result,
            status_values={},
            recovery_child=False,
        ):
            raise RuntimeError("vector_index_result_acceptance_missing")
        return True
    if vector_index_result is not None:
        raise ValueError("vector_index_result_task_domain_mismatch")
    return False


def persist_forwarded_execution_status(
    *,
    job_id: str,
    response: Mapping[str, Any],
    status_values: Mapping[str, Any],
    recovery_child: bool,
    authoritative_recovery_task: Any,
    vector_index_result: Mapping[str, Any] | None,
    accept_vector_result: Callable[..., bool],
    update_task_status: Callable[..., Any],
    bound_knowledge_index_result: Mapping[str, Any] | None = None,
    publish_bound_knowledge_index_result: Callable[..., Any] | None = None,
) -> None:
    """Commit a forwarded result through exactly one domain-owned path.

    The callbacks keep the forwarding module's existing monkeypatch seams
    intact while this focused helper owns the mutually exclusive Vector,
    recovery, and generic status policy.
    """

    if accept_vector_result(
        job_id=job_id,
        result=vector_index_result,
        status_values=status_values,
        recovery_child=recovery_child,
    ):
        return
    if bound_knowledge_index_result is not None:
        if recovery_child:
            raise ValueError(
                "knowledge_index_result_recovery_task_forbidden"
            )
        if publish_bound_knowledge_index_result is None:
            raise RuntimeError(
                "knowledge_index_task_result_publisher_missing"
            )
        publish_bound_knowledge_index_result(
            job_id=job_id,
            result=bound_knowledge_index_result,
            status_values=status_values,
        )
        return
    if recovery_child:
        current_status = str(
            getattr(authoritative_recovery_task, "status", "") or ""
        ).strip().lower()
        if current_status in {
            "completed",
            "failed",
            "cancelled",
            "verification_failed",
            "skipped",
            "aborted",
            "timeout",
            "archived",
        }:
            raise RuntimeError("recovery_result_terminal_before_commit")
        update_task_status(
            job_id,
            current_status or "in_progress",
            **dict(status_values),
        )
        return
    update_task_status(
        job_id,
        str(response["status"]),
        **dict(status_values),
    )
