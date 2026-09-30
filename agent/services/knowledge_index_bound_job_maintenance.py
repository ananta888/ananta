"""Maintenance of bound knowledge-index executions: retry and expired dispatch.

Split out of ``knowledge_index_job_service`` (SRP):

* a retry goes through the execution binding service and re-persists the
  bound execution envelope only over the exact envelope the Hub task held;
* once the binding service reports that a Hub assignment or dispatch expired,
  the Hub task is failed exactly once through an atomic, envelope-bound status
  CAS; a concurrent identical projection is accepted, anything else is a
  conflict.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from agent.services.knowledge_index_bound_dispatch_gate import (
    persist_bound_execution_envelope,
)
from agent.services.knowledge_index_bound_task_projection import (
    expired_dispatch_reconciliation_marker,
    project_expired_dispatch_failure,
    task_has_expired_dispatch_projection,
    task_matches_bound_execution,
)
from agent.services.knowledge_index_job_contract import RECONCILABLE_TASK_STATUSES
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
)


def retry_bound_execution(
    *,
    binding_service: Any,
    repository_provider: Callable[[], Any],
    job_id: str,
    assignment: Mapping[str, Any],
    **retry_options: Any,
) -> Any:
    """Retry through the binding service and persist the new envelope; return the execution record."""

    from ananta_contracts.knowledge_index_execution import (
        KnowledgeIndexExecutionAssignment,
    )

    service = binding_service
    if service is None:
        raise RuntimeError(
            "knowledge_index_execution_binding_service_unavailable"
        )
    task = repository_provider().get_by_id(str(job_id))
    if task is None:
        raise ValueError("knowledge_index_job_not_found")
    raw_task = (
        task.model_dump()
        if hasattr(task, "model_dump")
        else dict(task)
    )
    expected_envelope = dict(
        dict(raw_task.get("worker_execution_context") or {}).get(
            "knowledge_index_job"
        )
        or {}
    )
    record = service.retry(
        job_id=str(job_id),
        assignment=KnowledgeIndexExecutionAssignment.model_validate(
            dict(assignment)
        ),
        **retry_options,
    )
    persist_bound_execution_envelope(
        repository_provider(),
        job_id=str(job_id),
        expected_envelope=expected_envelope,
        envelope=record.job.to_wire(),
    )
    return record


def reconcile_expired_bound_dispatch(
    *,
    binding_service: Any,
    repository_provider: Callable[[], Any],
    job_id: str,
    expected_lock_version: int,
) -> dict[str, Any]:
    """Project one expired Hub assignment or dispatch without replay."""

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise ValueError("knowledge_index_job_not_found")
    try:
        normalized_lock_version = int(expected_lock_version)
    except (TypeError, ValueError):
        normalized_lock_version = 0
    if isinstance(expected_lock_version, bool) or normalized_lock_version < 1:
        raise ValueError("knowledge_index_execution_lock_version_invalid")
    if binding_service is None:
        raise RuntimeError(
            "knowledge_index_execution_binding_service_unavailable"
        )
    reconcile = getattr(
        binding_service,
        "reconcile_expired_dispatch",
        None,
    )
    if not callable(reconcile):
        raise RuntimeError(
            "knowledge_index_execution_reconcile_service_unavailable"
        )
    record = reconcile(
        job_id=normalized_job_id,
        expected_lock_version=normalized_lock_version,
    )
    if record.state != "failed" or record.completed_at_epoch_ms is None:
        raise RuntimeError(
            "knowledge_index_execution_reconcile_result_invalid"
        )

    repository = repository_provider()
    status_cas = getattr(repository, "compare_and_set_status", None)
    if not callable(status_cas):
        raise RuntimeError(
            "knowledge_index_atomic_task_status_repository_required"
        )
    marker = expired_dispatch_reconciliation_marker(record)
    result = status_cas(
        normalized_job_id,
        expected_statuses=set(RECONCILABLE_TASK_STATUSES),
        target_status="failed",
        predicate=lambda task: task_matches_bound_execution(
            task,
            expected_envelope=record.job.to_wire(),
        ),
        mutate=lambda task: project_expired_dispatch_failure(
            task,
            marker=marker,
        ),
    )
    projected_task = getattr(result, "task", None)
    if not bool(getattr(result, "updated", False)) and not (
        projected_task is not None
        and task_has_expired_dispatch_projection(
            projected_task,
            marker=marker,
        )
    ):
        raise ValueError(
            "knowledge_index_execution_task_projection_conflict"
        )
    return {
        "job_id": normalized_job_id,
        "status": "failed",
        "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
        "execution_state": record.state,
        "execution_lock_version": int(record.lock_version),
        "completed_at_epoch_ms": int(record.completed_at_epoch_ms),
    }


__all__ = ["reconcile_expired_bound_dispatch", "retry_bound_execution"]
