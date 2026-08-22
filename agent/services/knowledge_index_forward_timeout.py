"""Hub-authoritative deadline policy for governed knowledge-index transport."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Callable

from agent.services.knowledge_index_job_service import (
    KNOWLEDGE_INDEX_JOB_SCHEMA,
)
from agent.services.worker_forward_transport import (
    WorkerTransportDeadline,
)
from ananta_contracts.knowledge_index_dispatch import (
    SOURCE_ACCESS_MANIFEST_FIELD,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS,
    KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KnowledgeIndexExecutionContractError,
    parse_execution_job,
)


def _validated_execution_job(task: Mapping[str, Any]):
    worker_context = task.get("worker_execution_context")
    bound_job = (
        worker_context.get("knowledge_index_job")
        if isinstance(worker_context, Mapping)
        else None
    )
    if not isinstance(bound_job, Mapping):
        raise ValueError("knowledge_index_execution_job_invalid")

    # The enforcement manifest is a Hub-issued dispatch decoration, not part of
    # the immutable closed execution-job contract.  No other extra field is
    # removed: full v2 parsing must remain fail-closed.
    immutable_job = dict(bound_job)
    immutable_job.pop(SOURCE_ACCESS_MANIFEST_FIELD, None)
    try:
        parsed_job = parse_execution_job(immutable_job)
    except KnowledgeIndexExecutionContractError as exc:
        raise ValueError(str(exc)) from exc

    task_id = str(task.get("id") or "").strip()
    if not task_id or parsed_job.job_id != task_id:
        raise ValueError("knowledge_index_execution_job_task_mismatch")
    return parsed_job


def resolve_knowledge_index_forward_budget_seconds(
    task: Mapping[str, Any],
    *,
    dispatch_phase: str,
) -> int | None:
    """Return the exact transport budget from a fully parsed persisted job."""

    if (
        str(task.get("task_kind") or "").strip().lower()
        != "codecompass_index_build"
    ):
        return None
    if str(dispatch_phase or "").strip().lower() != "execute":
        return None
    worker_context = task.get("worker_execution_context")
    bound_job = (
        worker_context.get("knowledge_index_job")
        if isinstance(worker_context, Mapping)
        else None
    )
    if not isinstance(bound_job, Mapping):
        raise ValueError("knowledge_index_execution_binding_missing")
    bound_schema = str(bound_job.get("schema") or "").strip()
    # v1 is a public compatibility contract.  Deadline/claim hardening is
    # intentionally restricted to an explicitly bound v2 execution job.
    if bound_schema == KNOWLEDGE_INDEX_JOB_SCHEMA:
        return None
    if bound_schema != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA:
        raise ValueError("knowledge_index_execution_binding_schema_unknown")
    parsed_job = _validated_execution_job(task)
    full_budget_seconds = (
        parsed_job.resources.max_runtime_seconds
        + KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
    )
    manifest = bound_job.get(SOURCE_ACCESS_MANIFEST_FIELD)
    if not isinstance(manifest, Mapping):
        return full_budget_seconds
    grant_expires_epoch_ms = manifest.get(
        "grant_expires_at_epoch_ms"
    )
    if grant_expires_epoch_ms is None:
        return full_budget_seconds
    if (
        isinstance(grant_expires_epoch_ms, bool)
        or not isinstance(grant_expires_epoch_ms, int)
    ):
        raise ValueError(
            "knowledge_index_exact_retry_authority_window_invalid"
        )
    remaining_authority_ms = (
        min(
            parsed_job.assignment.lease_expires_epoch_ms,
            grant_expires_epoch_ms,
        )
        - int(time.time() * 1000)
    )
    remaining_budget_seconds = remaining_authority_ms // 1000
    if remaining_budget_seconds < 1:
        raise ValueError(
            KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
        )
    return min(full_budget_seconds, remaining_budget_seconds)


def resolve_knowledge_index_forward_deadline(
    task: Mapping[str, Any],
    *,
    dispatch_phase: str,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> WorkerTransportDeadline | None:
    """Create one absolute deadline to be shared by all transport attempts."""

    budget_seconds = resolve_knowledge_index_forward_budget_seconds(
        task,
        dispatch_phase=dispatch_phase,
    )
    if budget_seconds is None:
        return None
    return WorkerTransportDeadline.after_seconds(
        budget_seconds,
        monotonic_clock=monotonic_clock,
    )


def resolve_knowledge_index_forward_timeout_seconds(
    task: Mapping[str, Any],
    *,
    dispatch_phase: str,
) -> int | None:
    """Compatibility view of the authoritative budget (not a retry clock)."""

    return resolve_knowledge_index_forward_budget_seconds(
        task,
        dispatch_phase=dispatch_phase,
    )


__all__ = [
    "resolve_knowledge_index_forward_budget_seconds",
    "resolve_knowledge_index_forward_deadline",
    "resolve_knowledge_index_forward_timeout_seconds",
]
