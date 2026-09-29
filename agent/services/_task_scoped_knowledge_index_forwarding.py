"""Governed knowledge-index forwarding: bounded retries and result publication.

Split out of ``_task_scoped_forwarding`` (SRP): polling a governed
knowledge-index worker while its typed result is pending (inside the job's
retry window) and materializing/publishing the forwarded index result on the
Hub. The retry limits stay defined (and patchable) on ``_task_scoped_forwarding``
and are read through that facade at call time.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from typing import Any, Callable

from agent.services._task_scoped_codecompass_dispatch import _permanent_codecompass_forwarding_error
from agent.services.worker_forward_transport import (
    WorkerForwardTransportError,
    WorkerTransportDeadline,
    invoke_worker_forwarder,
)
from ananta_contracts.knowledge_index_dispatch import (
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
    SOURCE_ACCESS_MANIFEST_FIELD,
)


def _facade():
    """Resolve patchable collaborators through the public ``_task_scoped_forwarding`` entry point."""
    import agent.services._task_scoped_forwarding as facade_module

    return facade_module


def _is_typed_knowledge_index_result_pending(
    response: Any,
) -> bool:
    """Accept only the Worker error shape owned by the dispatch contract."""

    if not isinstance(response, Mapping):
        return False
    try:
        http_status = int(response.get("http_status") or 0)
    except (TypeError, ValueError):
        return False
    details = response.get("details")
    if not isinstance(details, Mapping):
        return False
    reason_details = details.get("details")
    return bool(
        str(response.get("status") or "").strip().lower() == "error"
        and http_status
        == KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS
        and response.get("message")
        == KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
        and details.get("error_type")
        == KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE
        and details.get("retryable") is True
        and isinstance(reason_details, Mapping)
        and reason_details.get("reason_code")
        == KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
    )


def _governed_knowledge_index_retry_expiries(
    *,
    task: Mapping[str, Any],
    prepared_payload: Mapping[str, Any],
) -> tuple[int, int]:
    context = task.get("worker_execution_context")
    job = (
        context.get("knowledge_index_job")
        if isinstance(context, Mapping)
        else None
    )
    assignment = job.get("assignment") if isinstance(job, Mapping) else None
    marker = prepared_payload.get("knowledge_index_dispatch")
    manifest = (
        marker.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if isinstance(marker, Mapping)
        else None
    )
    lease_expires = (
        assignment.get("lease_expires_epoch_ms")
        if isinstance(assignment, Mapping)
        else None
    )
    grant_expires = (
        manifest.get("grant_expires_at_epoch_ms")
        if isinstance(manifest, Mapping)
        else None
    )
    if (
        isinstance(lease_expires, bool)
        or not isinstance(lease_expires, int)
        or isinstance(grant_expires, bool)
        or not isinstance(grant_expires, int)
    ):
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_exact_retry_authority_window_invalid"
        )
    return lease_expires, grant_expires


def _require_governed_knowledge_index_retry_window(
    *,
    task: Mapping[str, Any],
    prepared_payload: Mapping[str, Any],
    transport_deadline: WorkerTransportDeadline,
) -> float:
    """Keep exact replay inside the original deadline and capabilities."""

    remaining_transport = transport_deadline.require_remaining_seconds()
    lease_expires, grant_expires = (
        _governed_knowledge_index_retry_expiries(
            task=task,
            prepared_payload=prepared_payload,
        )
    )
    now_epoch_ms = int(time.time() * 1000)
    if now_epoch_ms >= lease_expires:
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_execution_lease_stale"
        )
    if now_epoch_ms >= grant_expires:
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_source_access_grant_expired"
        )
    remaining_authority = (
        min(lease_expires, grant_expires) - now_epoch_ms
    ) / 1000.0
    return min(remaining_transport, remaining_authority)


def _invoke_governed_knowledge_index_forwarder(
    *,
    enabled: bool,
    task: Mapping[str, Any],
    forwarder: Callable[..., Any],
    worker_url: str,
    endpoint: str,
    prepared_payload: Mapping[str, Any],
    token: str,
    transport_deadline: WorkerTransportDeadline | None,
) -> Any:
    """Retry only an exact governed-v2 execute request under one authority."""

    frozen_payload = copy.deepcopy(dict(prepared_payload))
    if not enabled:
        return invoke_worker_forwarder(
            forwarder,
            worker_url,
            endpoint,
            frozen_payload,
            token=token,
            transport_deadline=transport_deadline,
        )
    if transport_deadline is None:
        raise _permanent_codecompass_forwarding_error(
            "worker_forward_transport_deadline_missing"
        )
    for attempt in range(
        1,
        _facade()._GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS + 1,
    ):
        response_lost = False
        try:
            response = invoke_worker_forwarder(
                forwarder,
                worker_url,
                endpoint,
                copy.deepcopy(frozen_payload),
                token=token,
                transport_deadline=transport_deadline,
            )
        except WorkerForwardTransportError as exc:
            if not exc.retryable:
                raise
            response = None
            response_lost = True
        result_pending = (
            False
            if response_lost
            else _is_typed_knowledge_index_result_pending(response)
        )
        if not response_lost and not result_pending:
            return response
        if attempt >= _facade()._GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS:
            reason_code = (
                "knowledge_index_worker_dispatch_result_pending_retry_exhausted"
                if result_pending
                else "knowledge_index_worker_response_loss_retry_exhausted"
            )
            raise WorkerForwardTransportError(
                reason_code,
                retryable=True,
            )
        remaining = _require_governed_knowledge_index_retry_window(
            task=task,
            prepared_payload=frozen_payload,
            transport_deadline=transport_deadline,
        )
        if result_pending:
            time.sleep(
                min(
                    _facade()._GOVERNED_KNOWLEDGE_INDEX_PENDING_POLL_SECONDS,
                    remaining,
                )
            )
            _require_governed_knowledge_index_retry_window(
                task=task,
                prepared_payload=frozen_payload,
                transport_deadline=transport_deadline,
            )
    raise AssertionError("unreachable")


def _materialize_forwarded_knowledge_index_result(
    *,
    tid: str,
    response: Mapping[str, Any],
    task: Mapping[str, Any],
    transport_deadline: WorkerTransportDeadline | None,
) -> dict[str, Any] | None:
    """Admit one knowledge-index result through its versioned Hub port."""

    result_schema = str(response.get("schema") or "")
    if result_schema not in {
        "ananta.knowledge_index_job_result.v1",
        "ananta.knowledge_index_execution_result.v2",
    }:
        return None
    result_fields = {
        "schema",
        "job_id",
        "idempotency_fingerprint",
        "status",
        "reason_code",
        "knowledge_index",
        "run",
        "results",
        "artifact_refs",
        "error",
    }
    framework_fields = {"handler_contract"}
    if result_schema == "ananta.knowledge_index_execution_result.v2":
        candidate = {
            field: value
            for field, value in response.items()
            if field not in framework_fields
        }
    else:
        unknown_fields = set(response) - result_fields - framework_fields
        if unknown_fields:
            raise ValueError(
                "knowledge_index_result_forwarding_fields_unknown"
            )
        candidate = {
            field: response.get(field) for field in result_fields
        }
    execution_context = dict(
        task.get("worker_execution_context") or {}
    )
    execution_job = dict(
        execution_context.get("knowledge_index_job") or {}
    )
    if (
        execution_job.get("schema")
        == "ananta.knowledge_index_execution_job.v2"
    ):
        job_service = _facade()._governed_source_control_index_job_service()
        assigned_worker_url = str(
            task.get("assigned_agent_url") or ""
        ).strip()
        assigned_worker = (
            _facade().get_repository_registry().agent_repo.get_by_url(
                assigned_worker_url
            )
            if assigned_worker_url
            else None
        )
        authenticated_worker_id = str(
            getattr(assigned_worker, "name", "") or ""
        ).strip()
        if not authenticated_worker_id:
            raise ValueError(
                "knowledge_index_result_worker_identity_missing"
            )
    else:
        job_service = _facade().get_core_services().knowledge_index_job_service
        authenticated_worker_id = None
    return job_service.materialize_worker_result(
        job_id=tid,
        result=candidate,
        task=task,
        authenticated_worker_id=authenticated_worker_id,
        transfer_deadline=transport_deadline,
    )


def _publish_forwarded_bound_knowledge_index_result(
    *,
    job_id: str,
    result: Mapping[str, Any],
    status_values: Mapping[str, Any],
) -> None:
    """Commit the accepted v2 result through its Hub-owned Task CAS."""

    _facade()._governed_source_control_index_job_service().publish_bound_task_result(
        job_id=str(job_id),
        result=dict(result),
        status_values=dict(status_values),
    )
