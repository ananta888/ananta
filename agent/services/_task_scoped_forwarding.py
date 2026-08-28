"""Forwarding-hub cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
forwarding_hub cluster of SPLIT-001 (sub-split 001f). The module owns
cross-container task forwarding: deciding whether to forward to a remote
worker, persisting forwarded proposal/execution results, and normalizing
forwarded artifacts.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable, Protocol
from urllib.parse import urlparse

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import WorkerForwardingError
from agent.config import settings
from agent.llm_integration import normalize_llm_call_profile_entry
from agent.services._vector_index_result_forwarding import (
    accept_bound_forwarded_vector_index_result,
    accept_forwarded_vector_index_result,
    is_authoritative_vector_index_task,
    vector_index_result_candidate,
)
from agent.services._vector_index_result_forwarding import (
    persist_forwarded_execution_status as _persist_forwarded_execution_status,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.task_runtime_service import update_local_task_status
from agent.services.worker_forward_outcome import (
    get_worker_forward_outcome_recorder,
)
from agent.services.worker_forward_transport import (
    DeadlineAwareWorkerForwarder,
    WorkerForwardDeadlineExceeded,
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

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


_GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS = 16
_GOVERNED_KNOWLEDGE_INDEX_PENDING_POLL_SECONDS = 0.25


class DeadlineAwareForwardResultAcceptor(Protocol):
    """Hub-local result port sharing the original transport deadline."""

    def __call__(
        self,
        response: dict[str, Any],
        task: dict[str, Any],
        *,
        transport_deadline: WorkerTransportDeadline,
    ) -> None: ...


def _accept_forwarded_worker_result(
    acceptor: Callable[..., None],
    response: dict[str, Any],
    task: dict[str, Any],
    *,
    transport_deadline: WorkerTransportDeadline | None,
) -> None:
    """Run Hub result admission under the same immutable POST deadline."""

    if transport_deadline is None:
        acceptor(response, task)
        return
    transport_deadline.require_remaining_seconds()
    acceptor(
        response,
        task,
        transport_deadline=transport_deadline,
    )


def _is_codecompass_index_task(task: Mapping[str, Any]) -> bool:
    return str(task.get("task_kind") or "").strip().lower() == "codecompass_index_build"


def _has_governed_codecompass_binding(task: Mapping[str, Any]) -> bool:
    if not _is_codecompass_index_task(task):
        return False
    worker_execution_context = task.get("worker_execution_context")
    if not isinstance(worker_execution_context, Mapping):
        return False
    knowledge_index_job = worker_execution_context.get("knowledge_index_job")
    return bool(
        isinstance(knowledge_index_job, Mapping)
        and knowledge_index_job.get("schema")
        == "ananta.knowledge_index_execution_job.v2"
    )


def _has_public_codecompass_v1_binding(
    task: Mapping[str, Any],
) -> bool:
    if not _is_codecompass_index_task(task):
        return False
    worker_execution_context = task.get("worker_execution_context")
    knowledge_index_job = (
        worker_execution_context.get("knowledge_index_job")
        if isinstance(worker_execution_context, Mapping)
        else None
    )
    return bool(
        isinstance(knowledge_index_job, Mapping)
        and knowledge_index_job.get("schema")
        == "ananta.knowledge_index_job.v1"
    )


def _urls_resolve_same_runtime(
    worker_url: str,
    local_url: str,
    *,
    default_port: int,
) -> bool:
    try:
        parsed_worker = urlparse(worker_url)
        parsed_self = urlparse(local_url)
        worker_host = str(parsed_worker.hostname or "").strip().lower().rstrip(".")
        self_host = str(parsed_self.hostname or "").strip().lower().rstrip(".")
        worker_port = int(parsed_worker.port or default_port)
        self_port = int(parsed_self.port or default_port)
    except (TypeError, ValueError):
        return False
    worker_is_local = worker_host in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "::",
    }
    try:
        worker_address = ipaddress.ip_address(worker_host)
        worker_is_local = bool(
            worker_is_local
            or worker_address.is_loopback
            or worker_address.is_unspecified
            or (
                worker_address.version == 6
                and worker_address.ipv4_mapped is not None
                and (
                    worker_address.ipv4_mapped.is_loopback
                    or worker_address.ipv4_mapped.is_unspecified
                )
            )
        )
    except ValueError:
        pass
    return worker_port == self_port and (
        worker_is_local
        or worker_host == self_host
    )


def _permanent_codecompass_forwarding_error(
    reason_code: str,
    *,
    worker_url: str | None = None,
    details: Mapping[str, Any] | None = None,
    status_code: int = 409,
) -> WorkerForwardingError:
    error_details = dict(details or {})
    error_details.setdefault("details", reason_code)
    if worker_url:
        error_details.setdefault("worker_url", worker_url)
    error_details["reason_code"] = reason_code
    return WorkerForwardingError(
        reason_code,
        details=error_details,
        status_code=status_code,
        retryable=False,
    )


def _requires_governed_codecompass_transport(
    task: Mapping[str, Any],
) -> bool:
    """Classify the exact public-v1/governed-v2 compatibility boundary."""

    if not _is_codecompass_index_task(task):
        return False
    if _has_governed_codecompass_binding(task):
        return True
    if _has_public_codecompass_v1_binding(task):
        return False
    raise _permanent_codecompass_forwarding_error(
        "knowledge_index_execution_binding_missing",
        worker_url=task.get("assigned_agent_url"),
    )


def _normalize_forwarded_step_envelope(response: Any) -> dict[str, Any]:
    cursor = response
    for _depth in range(6):
        if not isinstance(cursor, dict) or "data" not in cursor:
            break
        nested = cursor.get("data")
        if not isinstance(nested, dict):
            return {}
        cursor = nested
    normalized = unwrap_api_envelope(response)
    return normalized if isinstance(normalized, dict) else {}


def _prepare_codecompass_worker_dispatch(
    *,
    enabled: bool,
    tid: str,
    task: dict[str, Any],
    payload: dict[str, Any],
    registered_agent: Any,
    registered_worker_token: str,
    dispatch_phase: str,
) -> None:
    if not enabled:
        return
    _authorize_codecompass_worker_dispatch(
        tid=tid,
        task=task,
        registered_agent=registered_agent,
        registered_worker_token=registered_worker_token,
        dispatch_phase=dispatch_phase,
    )
    from ananta_contracts.knowledge_index_dispatch import (
        SOURCE_ACCESS_MANIFEST_FIELD,
        build_knowledge_index_dispatch,
    )

    worker_context = task.get("worker_execution_context")
    bound_job = (
        worker_context.get("knowledge_index_job")
        if isinstance(worker_context, Mapping)
        else None
    )
    if (
        not isinstance(bound_job, Mapping)
        or bound_job.get("schema")
        != "ananta.knowledge_index_execution_job.v2"
    ):
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_execution_binding_missing"
        )
    source_access_manifest = None
    if dispatch_phase == "execute":
        raw_manifest = bound_job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if not isinstance(raw_manifest, Mapping):
            raise _permanent_codecompass_forwarding_error(
                "knowledge_index_source_access_manifest_missing"
            )
        source_access_manifest = dict(raw_manifest)
    try:
        payload["knowledge_index_dispatch"] = (
            build_knowledge_index_dispatch(
                job_id=tid,
                phase=dispatch_phase,
                source_access_manifest=source_access_manifest,
            )
        )
    except ValueError as exc:
        raise _permanent_codecompass_forwarding_error(
            str(exc or "knowledge_index_dispatch_invalid")
        ) from exc


def _codecompass_execute_deadline(
    *,
    task: Mapping[str, Any],
    dispatch_phase: str,
) -> WorkerTransportDeadline | None:
    """Translate timeout-policy errors to the governed forwarding contract."""

    from agent.services.knowledge_index_forward_timeout import (
        resolve_knowledge_index_forward_deadline,
    )

    try:
        return resolve_knowledge_index_forward_deadline(
            task,
            dispatch_phase=dispatch_phase,
        )
    except ValueError as exc:
        raise _permanent_codecompass_forwarding_error(
            str(exc or "knowledge_index_resource_budget_invalid")
        ) from exc


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
        _GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS + 1,
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
        if attempt >= _GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS:
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
                    _GOVERNED_KNOWLEDGE_INDEX_PENDING_POLL_SECONDS,
                    remaining,
                )
            )
            _require_governed_knowledge_index_retry_window(
                task=task,
                prepared_payload=frozen_payload,
                transport_deadline=transport_deadline,
            )
    raise AssertionError("unreachable")


def _raise_forwarded_worker_http_error(
    response: Any,
    *,
    worker_url: str,
    endpoint: str,
) -> None:
    if not (
        isinstance(response, dict)
        and str(response.get("status") or "").strip().lower() == "error"
    ):
        return
    try:
        http_status = int(response.get("http_status") or 0)
    except (TypeError, ValueError):
        http_status = 0
    if 400 <= http_status <= 499:
        raw_details = response.get("details")
        details = raw_details if isinstance(raw_details, Mapping) else {}
        raw_nested_details = details.get("details")
        nested_details = (
            raw_nested_details
            if isinstance(raw_nested_details, Mapping)
            else {}
        )
        reason_code = str(
            (
                "worker_authentication_rejected"
                if http_status in {401, 403}
                else nested_details.get("reason_code")
                or details.get("reason_code")
                or response.get("reason_code")
                or response.get("message")
                or "worker_request_rejected"
            )
        ).strip()
        raise WorkerForwardingError(
            reason_code,
            details={
                "details": str(response.get("message") or ""),
                "reason_code": reason_code,
                "downstream_http_status": http_status,
                "worker_url": worker_url,
                "endpoint": endpoint,
            },
            status_code=http_status,
            retryable=False,
        )
    raise RuntimeError(
        f"worker_http_error:{worker_url}:{endpoint}:"
        f"status={http_status}:{str(response.get('message') or '')}"
    )


def _worker_404_hub_fallback_enabled() -> bool:
    try:
        policy = dict(
            current_app.config.get("AGENT_CONFIG", {}).get(
                "execution_fallback_policy"
            )
            or {}
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return True
    return bool(policy.get("worker_404_hub_fallback_enabled", True))


def _record_forwarded_worker_success(worker_url: str) -> None:
    try:
        recorder = get_worker_forward_outcome_recorder()
        if recorder is not None:
            recorder.record_worker_forward_success(worker_url)
    except Exception:
        current_app.logger.warning(
            "Worker outcome success observation failed for %s",
            worker_url,
        )


def _record_forwarded_worker_failure(
    worker_url: str,
    *,
    task_id: str,
    endpoint: str,
) -> None:
    try:
        recorder = get_worker_forward_outcome_recorder()
        if recorder is not None:
            recorder.record_worker_forward_failure(
                worker_url,
                "forwarded_worker_transport_failed",
                task_id=task_id,
                endpoint=endpoint,
            )
    except Exception:
        current_app.logger.warning(
            "Worker outcome failure observation failed for %s",
            worker_url,
        )


def _is_completion_projection_pending(exc: Exception) -> bool:
    """Classify the post-acceptance Hub saga without coupling transport to it."""

    from agent.services.knowledge_index_job_service import (
        KnowledgeIndexCompletionProjectionPending,
    )

    return isinstance(exc, KnowledgeIndexCompletionProjectionPending)


def _completion_projection_pending_response(
    *,
    enabled: bool,
    exc: Exception,
    task_id: str,
    worker_url: str,
    release_mail_lease: Callable[[], None],
) -> "TaskScopedRouteResponse | None":
    """Return the Hub-local continuation without blaming the Worker."""

    if not enabled or not _is_completion_projection_pending(exc):
        return None
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    # The Worker's bound result and completion outbox are already durable.
    # A second execute dispatch is forbidden; only the idempotent Hub
    # Source-Control projection remains.
    _record_forwarded_worker_success(worker_url)
    release_mail_lease()
    current_app.logger.warning(
        "Knowledge-index result accepted for task %s; "
        "Hub completion projection remains pending",
        task_id,
    )
    return TaskScopedRouteResponse(
        data={
            "status": "completion_projection_pending",
            "reason_code": "knowledge_index_source_projection_pending",
            "task_id": task_id,
            "worker_result_accepted": True,
            "worker_dispatch_retry_allowed": False,
            "reconciliation_required": True,
        },
        status="pending",
        message="Worker result accepted; Hub completion projection pending",
        code=202,
    )


def _handle_forwarding_failure(
    *,
    exc: Exception,
    governed_codecompass_v2: bool,
    worker_result_accepted: bool,
    worker_url: str,
    task_id: str,
    endpoint: str,
    preserve_mail_lease_on_error: bool,
    release_mail_lease: Callable[[], None],
) -> "TaskScopedRouteResponse":
    """Keep Worker health, local saga state and transport errors separate."""

    pending_response = _completion_projection_pending_response(
        enabled=governed_codecompass_v2,
        exc=exc,
        task_id=task_id,
        worker_url=worker_url,
        release_mail_lease=release_mail_lease,
    )
    if pending_response is not None:
        return pending_response
    if not worker_result_accepted:
        _record_forwarded_worker_failure(
            worker_url,
            task_id=task_id,
            endpoint=endpoint,
        )
    if preserve_mail_lease_on_error:
        current_app.logger.warning(
            "Recovery mail lease retained after result commit failure for task %s",
            task_id,
        )
    else:
        release_mail_lease()
    current_app.logger.error(
        "Forwarding an %s fehlgeschlagen: %s",
        worker_url,
        exc,
    )
    if isinstance(exc, WorkerForwardingError):
        raise exc
    if isinstance(exc, WorkerForwardTransportError):
        raise WorkerForwardingError(
            str(exc),
            details={
                "details": str(exc),
                "reason_code": exc.reason_code,
                "worker_url": worker_url,
                "retryable": exc.retryable,
            },
            status_code=(
                504
                if isinstance(exc, WorkerForwardDeadlineExceeded)
                else 502
            ),
            retryable=exc.retryable,
        ) from exc
    raise WorkerForwardingError(
        details={"details": str(exc), "worker_url": worker_url}
    ) from exc


def _governed_source_control_index_job_service() -> Any:
    service = current_app.extensions.get(
        "source_control_governed_knowledge_index_job_service"
    )
    if service is None:
        raise WorkerForwardingError(
            "knowledge_index_dispatch_authorizer_unavailable"
        )
    return service


def _authorize_codecompass_worker_dispatch(
    *,
    tid: str,
    task: dict[str, Any],
    registered_agent: Any,
    registered_worker_token: str,
    dispatch_phase: str,
) -> None:
    if registered_agent is None:
        raise _permanent_codecompass_forwarding_error(
            "assigned_worker_not_registered"
        )
    if not getattr(registered_agent, "registration_validated", False):
        raise _permanent_codecompass_forwarding_error(
            "assigned_worker_registration_not_validated"
        )
    if str(getattr(registered_agent, "role", "")).strip().lower() != "worker":
        raise _permanent_codecompass_forwarding_error(
            "assigned_agent_is_not_worker"
        )
    if str(getattr(registered_agent, "status", "")).strip().lower() not in {
        "online",
        "degraded",
        "busy",
    }:
        raise WorkerForwardingError(
            "assigned_worker_not_available",
            details={"details": "assigned_worker_not_available"},
            status_code=503,
            retryable=True,
        )
    if not registered_worker_token:
        raise WorkerForwardingError(
            "assigned_worker_token_missing",
            details={"details": "assigned_worker_token_missing"},
            status_code=503,
            retryable=True,
        )

    worker_execution_context = task.get("worker_execution_context")
    if not isinstance(worker_execution_context, Mapping):
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_execution_binding_missing"
        )
    destination_selection = worker_execution_context.get(
        "destination_selection"
    )
    if not isinstance(destination_selection, Mapping) or not destination_selection:
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_destination_selection_missing"
        )
    try:
        authorized_context = (
            _governed_source_control_index_job_service()
            .authorize_bound_worker_dispatch(
                job_id=tid,
                authenticated_worker_id=str(
                    registered_agent.name or registered_agent.url
                ),
                destination_selection=destination_selection,
                dispatch_phase=dispatch_phase,
            )
        )
    except WorkerForwardingError:
        raise
    except ValueError as exc:
        reason_code = str(exc or "knowledge_index_dispatch_preflight_rejected")
        raise _permanent_codecompass_forwarding_error(
            reason_code,
            worker_url=str(getattr(registered_agent, "url", "") or ""),
        ) from exc
    except Exception as exc:
        raise WorkerForwardingError(
            "knowledge_index_dispatch_preflight_unavailable",
            details={
                "details": str(exc),
                "reason_code": "knowledge_index_dispatch_preflight_unavailable",
                "worker_url": str(
                    getattr(registered_agent, "url", "") or ""
                ),
            },
            status_code=503,
            retryable=True,
        ) from exc
    task["worker_execution_context"] = {
        **dict(worker_execution_context),
        **authorized_context,
    }


_VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS: dict[str, tuple[str, frozenset[str]]] = {
    "visual_process_assistant_retrieval": (
        "ananta.visual_process_assistant.retrieval_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "status",
                "evidence",
                "rejected_count",
                "rejection_reasons",
                "consistency_state",
                "blocked_stubs",
                "evidence_conflicts",
            }
        ),
    ),
    "visual_process_assistant_inference": (
        "ananta.visual_process_assistant.inference_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "prompt_hash",
                "status",
                "reason_code",
                "response",
                "model_metadata",
            }
        ),
    ),
}
_VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS = frozenset(
    schema for schema, _fields in _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.values()
)
_FORWARDED_HANDLER_FRAMEWORK_FIELDS = frozenset({"handler_contract"})


def _get_visual_process_assistant_service() -> Any:
    """Resolve the Hub-owned service lazily and avoid a forwarding import cycle."""

    from agent.services.visual_process_assistant_service import (
        visual_process_assistant_service,
    )

    return visual_process_assistant_service


def _accept_visual_process_assistant_result(
    *,
    tid: str,
    response: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Dispatch a bound Visual Process worker result to its Hub owner.

    A Visual Process schema or task kind activates this fail-closed boundary.
    Framework metadata is validated separately and is never passed into the
    domain result contract.
    """

    task_kind = str(task.get("task_kind") or "").strip().lower()
    schema = str(response.get("schema") or "").strip()
    expected = _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.get(task_kind)
    if expected is None and schema not in _VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS:
        return None
    if expected is None:
        raise ValueError("visual_process_assistant_result_task_kind_unknown")
    expected_schema, result_fields = expected
    if schema != expected_schema:
        raise ValueError("visual_process_assistant_result_schema_kind_mismatch")

    handler_contract = response.get("handler_contract")
    if handler_contract is not None:
        if not isinstance(handler_contract, Mapping):
            raise ValueError("visual_process_assistant_handler_contract_invalid")
        handler_task_kind = str(handler_contract.get("task_kind") or "").strip().lower()
        if handler_task_kind != task_kind:
            raise ValueError("visual_process_assistant_handler_task_kind_mismatch")

    unknown_fields = set(response) - result_fields - _FORWARDED_HANDLER_FRAMEWORK_FIELDS
    if unknown_fields:
        raise ValueError("visual_process_assistant_result_forwarding_fields_unknown")
    candidate = {field: response.get(field) for field in result_fields}
    accepted = _get_visual_process_assistant_service().accept_worker_result(
        task_id=tid,
        result=candidate,
    )
    if not isinstance(accepted, Mapping):
        raise ValueError("visual_process_assistant_acceptance_readmodel_invalid")
    return dict(accepted)


def forward_task_request_if_remote(
    *,
    tid: str,
    task: dict,
    endpoint: str,
    payload: dict,
    forwarder: DeadlineAwareWorkerForwarder | Callable[..., Any],
    on_success: (
        Callable[[dict, dict], None]
        | DeadlineAwareForwardResultAcceptor
    ),
) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    mail_lease: dict[str, Any] | None = None
    mail_lease_owner: str | None = None
    preserve_mail_lease_on_error = False

    def release_mail_lease() -> None:
        nonlocal mail_lease
        if mail_lease is None:
            return
        try:
            from agent.services.mail_task_service import get_mail_task_service

            get_mail_task_service().release_lease(
                job_id=tid,
                fencing_token=int(mail_lease["fencing_token"]),
                owner_ref=mail_lease_owner,
            )
        finally:
            mail_lease = None

    # Hub owns cross-container routing. Worker containers must execute locally
    # and never re-forward step endpoints to avoid forwarding loops.
    if str(getattr(settings, "role", "") or "").strip().lower() != "hub":
        return None
    from agent.services.organization_task_dispatch_gate_service import (
        organization_research_requires_secure_delegation,
    )

    if organization_research_requires_secure_delegation(task):
        reason_code = "organization_research_secure_delegation_required"
        return TaskScopedRouteResponse(
            data={
                "status": "denied",
                "reason_code": reason_code,
                "task_id": tid,
            },
            status="denied",
            message=reason_code,
            code=409,
        )
    governed_codecompass_v2 = (
        _requires_governed_codecompass_transport(task)
    )
    worker_url = task.get("assigned_agent_url")
    if not worker_url:
        if governed_codecompass_v2:
            raise _permanent_codecompass_forwarding_error(
                "assigned_worker_missing"
            )
        return None
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    local_role = str(settings.role or "").strip().lower()
    if worker_url.rstrip("/") == my_url.rstrip("/"):
        if governed_codecompass_v2 and local_role == "hub":
            raise _permanent_codecompass_forwarding_error(
                "assigned_worker_must_be_remote",
                worker_url=str(worker_url),
            )
        return None
    if _urls_resolve_same_runtime(
        str(worker_url),
        str(my_url),
        default_port=int(settings.port),
    ):
        if governed_codecompass_v2 and local_role == "hub":
            raise _permanent_codecompass_forwarding_error(
                "assigned_worker_must_be_remote",
                worker_url=str(worker_url),
            )
        return None
    payload = dict(payload)
    is_vector_index_task = is_authoritative_vector_index_task(
        task
    )
    if is_vector_index_task:
        dispatch_phase = (
            "propose"
            if endpoint.rstrip("/").endswith("/step/propose")
            else "execute"
        )
        from agent.services.vector_index_task_service import (
            get_vector_index_task_service,
        )

        payload["vector_index_dispatch"] = (
            get_vector_index_task_service().issue_dispatch_attempt(
                job_id=tid,
                worker_audience=str(worker_url),
                phase=dispatch_phase,
                actor="hub-worker-forwarder",
            )
        )
    assigned_token = task.get("assigned_agent_token")
    resolved_token = assigned_token
    dispatch_lease_token = str(
        payload.get("dispatch_lease_token") or ""
    ).strip()
    endpoint_dispatch_phase = (
        "propose"
        if endpoint.rstrip("/").endswith("/step/propose")
        else "execute"
    )
    requested_dispatch_phase = str(
        payload.get("dispatch_lease_phase") or ""
    ).strip().lower()
    if (
        governed_codecompass_v2
        and requested_dispatch_phase
        and requested_dispatch_phase != endpoint_dispatch_phase
    ):
        raise _permanent_codecompass_forwarding_error(
            "knowledge_index_dispatch_phase_mismatch",
            worker_url=str(worker_url),
        )
    dispatch_phase = (
        endpoint_dispatch_phase
        if governed_codecompass_v2
        else requested_dispatch_phase or endpoint_dispatch_phase
    )
    from agent.services.recovery_dispatch_gate_service import (
        get_recovery_dispatch_gate_service,
        recovery_dispatch_request_fingerprint,
    )

    recovery_gate = get_recovery_dispatch_gate_service()
    recovery_child = recovery_gate.is_recovery_child(task)
    recovery_fenced = bool(
        dispatch_lease_token
        or recovery_child
    )
    requires_authenticated_forward = bool(
        recovery_fenced
        or is_vector_index_task
        or governed_codecompass_v2
    )
    if recovery_child and not dispatch_lease_token:
        return TaskScopedRouteResponse(
            data={
                "status": "skipped",
                "reason": "recovery_dispatch_lease_missing",
                "task_id": tid,
                "phase": dispatch_phase,
            },
            status="skipped",
            message="Recovery dispatch requires a lease",
            code=409,
        )
    registered_agent = None
    registered_worker_token = ""
    try:
        registered_agent = get_repository_registry().agent_repo.get_by_url(
            worker_url
        )
        registered_worker_token = str(
            getattr(registered_agent, "token", "") or ""
        ).strip()
        if registered_worker_token:
            resolved_token = registered_worker_token
    except Exception:
        pass
    # Freeze the one governed deadline before authority preparation. This
    # prevents grant/lease checks performed during preparation from being
    # followed by a fresh full runtime window. Preparation, every POST retry,
    # result download, and Hub materialization all consume the same clock.
    transport_deadline = _codecompass_execute_deadline(
        task=task,
        dispatch_phase=dispatch_phase,
    )
    _prepare_codecompass_worker_dispatch(
        enabled=governed_codecompass_v2,
        tid=tid,
        task=task,
        payload=payload,
        registered_agent=registered_agent,
        registered_worker_token=registered_worker_token,
        dispatch_phase=dispatch_phase,
    )
    if not resolved_token:
        raise WorkerForwardingError(
            "assigned_worker_token_missing",
            details={
                "details": "assigned_worker_token_missing",
                "worker_url": worker_url,
            }
        )
    if (
        str(task.get("task_kind") or "").strip().lower() == "mail_operation"
        and str(endpoint or "").rstrip("/").endswith("/execute")
    ):
        from agent.services.mail_task_service import get_mail_task_service

        mail_lease_owner = (
            "hub-worker:"
            + hashlib.sha256(str(worker_url).encode("utf-8")).hexdigest()[:24]
        )
        mail_lease = get_mail_task_service().claim_for_delegation(
            job_id=tid,
            owner_ref=mail_lease_owner,
        )
        if mail_lease is None:
            raise WorkerForwardingError(
                details={
                    "details": "mail_task_account_lease_unavailable",
                    "worker_url": worker_url,
                }
            )
    worker_result_accepted = False
    try:
        response = _invoke_governed_knowledge_index_forwarder(
            enabled=bool(
                governed_codecompass_v2
                and dispatch_phase == "execute"
            ),
            task=task,
            forwarder=forwarder,
            worker_url=str(worker_url),
            endpoint=endpoint,
            prepared_payload=payload,
            token=str(resolved_token),
            transport_deadline=transport_deadline,
        )
        # Worker returned 404: task not in worker DB (split-DB dev setup).
        # Configurable via execution_fallback_policy.worker_404_hub_fallback_enabled.
        if (
            isinstance(response, dict)
            and str(response.get("status") or "").strip().lower() == "error"
            and int(response.get("http_status") or 0) == 404
            and not requires_authenticated_forward
            and _worker_404_hub_fallback_enabled()
        ):
            _record_forwarded_worker_failure(
                str(worker_url),
                task_id=tid,
                endpoint=endpoint,
            )
            release_mail_lease()
            current_app.logger.warning(
                "Worker %s returned 404 for %s — falling back to local hub execution",
                worker_url,
                endpoint,
            )
            return None
        _raise_forwarded_worker_http_error(
            response,
            worker_url=str(worker_url),
            endpoint=endpoint,
        )
        response = _normalize_forwarded_step_envelope(response)
        if not response:
            raise RuntimeError(f"worker_empty_payload:{worker_url}:{endpoint}")
        if isinstance(response, dict):
            if recovery_fenced:
                rejected_response = None
                with recovery_gate.result_guard(
                    tid,
                    token=dispatch_lease_token or None,
                    phase=dispatch_phase,
                    request_fingerprint=(
                        recovery_dispatch_request_fingerprint(
                            dispatch_phase,
                            payload,
                        )
                    ),
                    worker_url=str(worker_url),
                ) as decision:
                    if not decision.allowed:
                        rejected_response = TaskScopedRouteResponse(
                            data={
                                "status": "skipped",
                                "reason": decision.reason_code,
                                "task_id": tid,
                                "phase": dispatch_phase,
                            },
                            status="skipped",
                            message=(
                                "Recovery dispatch result rejected"
                            ),
                            code=409,
                        )
                    else:
                        preserve_mail_lease_on_error = bool(
                            mail_lease is not None
                        )
                        _accept_forwarded_worker_result(
                            on_success,
                            response,
                            task,
                            transport_deadline=transport_deadline,
                        )
                if rejected_response is not None:
                    release_mail_lease()
                    return rejected_response
                preserve_mail_lease_on_error = False
            else:
                _accept_forwarded_worker_result(
                    on_success,
                    response,
                    task,
                    transport_deadline=transport_deadline,
                )
            worker_result_accepted = True
            _record_forwarded_worker_success(str(worker_url))
            release_mail_lease()
        return TaskScopedRouteResponse(data=response)
    except Exception as exc:
        return _handle_forwarding_failure(
            exc=exc,
            governed_codecompass_v2=governed_codecompass_v2,
            worker_result_accepted=worker_result_accepted,
            worker_url=str(worker_url),
            task_id=tid,
            endpoint=endpoint,
            preserve_mail_lease_on_error=preserve_mail_lease_on_error,
            release_mail_lease=release_mail_lease,
        )


def persist_forwarded_proposal(
    response: dict,
    task: dict,
    request_payload: dict | None = None,
    *,
    allow_synthetic_llm_profile_fallback: Callable[[], bool],
) -> None:
    if not isinstance(response, dict):
        return
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )
    from agent.services.recovery_worker_result_service import (
        get_recovery_worker_result_service,
    )

    recovery_role = recovery_task_role(task)
    worker_result_service = get_recovery_worker_result_service()
    if recovery_role == "child":
        # Validate before proposal/history persistence.  A malformed Worker
        # envelope must have no authoritative Hub side effect.
        worker_result_service.merge_response(
            task_id=str(task["id"]),
            phase="propose",
            response=response,
            verification_status=task.get("verification_status"),
        )
    elif response.get("recovery_worker_result") is not None:
        raise ValueError("recovery_worker_result_unexpected")
    request_payload = dict(request_payload or {})
    prompt_text = str(request_payload.get("prompt") or "").strip()
    forwarded_request = {
        "prompt_preview": prompt_text[:240],
        "prompt_hash_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None,
        "provider": str(request_payload.get("provider") or "").strip() or None,
        "providers": (
            list(request_payload.get("providers") or [])
            if isinstance(request_payload.get("providers"), list)
            else None
        ),
        "model": str(request_payload.get("model") or "").strip() or None,
        "temperature": request_payload.get("temperature"),
        "strategy_mode": str(request_payload.get("strategy_mode") or "").strip() or None,
        "request_task_id": str(request_payload.get("task_id") or "").strip() or None,
        "captured_at": time.time(),
    }
    has_proposal_payload = any(
        key in response
        for key in (
            "command",
            "tool_calls",
            "reason",
            "raw",
            "routing",
            "cli_result",
            "trace",
            "review",
            "pipeline",
            "research_artifact",
            "research_context",
            "worker_context",
        )
    )
    if not has_proposal_payload:
        return
    response_trace = response.get("trace") if isinstance(response.get("trace"), dict) else None
    if not response_trace:
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        wrapped = response.get("proposal") if isinstance(response.get("proposal"), dict) else {}
        wrapped_meta = wrapped.get("metadata") if isinstance(wrapped.get("metadata"), dict) else {}
        prompt_trace_id = (
            str(metadata.get("prompt_trace_id") or "").strip()
            or str(wrapped_meta.get("prompt_trace_id") or "").strip()
        )
        if prompt_trace_id:
            response_trace = {
                "trace_id": prompt_trace_id,
                "source": "model_invocation_service",
                "request_kind": "propose",
            }
        else:
            response_trace = {
                "source": "external_worker_uninspectable",
                "request_kind": "propose",
                "external_worker_uninspectable": True,
            }
    cli_result = response.get("cli_result") if isinstance(response.get("cli_result"), dict) else None
    if not isinstance(cli_result, dict):
        response_meta = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        meta_profile = [
            normalize_llm_call_profile_entry(entry)
            for entry in list(response_meta.get("llm_call_profile") or [])
            if isinstance(entry, dict)
        ]
        if meta_profile:
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
                "llm_call_profile": meta_profile,
            }
    if cli_result is None:
        snapshot = get_core_services().autopilot_decision_service.build_proposal_snapshot(response)
        snapshot_cli = snapshot.get("cli_result") if isinstance(snapshot.get("cli_result"), dict) else None
        if isinstance(snapshot_cli, dict):
            cli_result = dict(snapshot_cli)
    if not isinstance(cli_result, dict) and allow_synthetic_llm_profile_fallback():
        backend = str(response.get("backend") or "orchestrator").strip() or "orchestrator"
        model = str(response.get("model") or "").strip() or None
        provider = None
        ms = response.get("model_selection")
        if isinstance(ms, dict):
            provider = str(ms.get("runtime_provider") or "").strip() or None
            model = model or (str(ms.get("selected_model") or "").strip() or None)
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": backend,
            "llm_call_profile": [
                {
                    "name": "propose_forwarded",
                    "backend": backend,
                    "provider": provider,
                    "model": model,
                    "success": True,
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "source": "orchestrator_synthetic",
                    "estimated": True,
                    "error_type": None,
                    "error_message": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ],
        }
    if not isinstance(cli_result, dict):
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
        }
    get_core_services().task_execution_service.persist_task_proposal_result(
        tid=task["id"],
        task=task,
        reason=str(response.get("reason") or ""),
        raw=str(response.get("raw") or ""),
        backend=(str(response.get("backend") or "").strip() or None),
        model=(str(response.get("model") or "").strip() or None),
        routing=response.get("routing") if isinstance(response.get("routing"), dict) else None,
        cli_result=cli_result,
        worker_context=response.get("worker_context") if isinstance(response.get("worker_context"), dict) else None,
        trace=response_trace,
        review=response.get("review") if isinstance(response.get("review"), dict) else None,
        pipeline=response.get("pipeline") if isinstance(response.get("pipeline"), dict) else None,
        command=(str(response.get("command") or "").strip() or None),
        tool_calls=response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None,
        comparisons=response.get("comparisons") if isinstance(response.get("comparisons"), dict) else None,
        research_artifact=(
            response.get("research_artifact")
            if isinstance(response.get("research_artifact"), dict)
            else None
        ),
        research_context=(
            response.get("research_context")
            if isinstance(response.get("research_context"), dict)
            else None
        ),
        forwarded_request=forwarded_request,
        history_event={
            "event_type": "proposal_result",
            "reason": str(response.get("reason") or ""),
            "backend": response.get("backend"),
            "routing_reason": (
                (response.get("routing") or {}).get("reason")
                if isinstance(response.get("routing"), dict)
                else None
            ),
            "forwarded_request": forwarded_request,
            "forwarded": True,
            "timestamp": time.time(),
        },
    )
    worker_result_service.accept_proposal_response(
        task_id=str(task["id"]),
        response=response,
    )


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
        job_service = _governed_source_control_index_job_service()
        assigned_worker_url = str(
            task.get("assigned_agent_url") or ""
        ).strip()
        assigned_worker = (
            get_repository_registry().agent_repo.get_by_url(
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
        job_service = get_core_services().knowledge_index_job_service
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

    _governed_source_control_index_job_service().publish_bound_task_result(
        job_id=str(job_id),
        result=dict(result),
        status_values=dict(status_values),
    )


def persist_forwarded_execution(
    *,
    tid: str,
    response: dict,
    task: dict,
    request_data,
    last_proposal: dict | None = None,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> None:
    if accept_bound_forwarded_vector_index_result(
        job_id=tid,
        response=response,
        task=task,
        load_task=get_repository_registry().task_repo.get_by_id,
        classify_task=is_authoritative_vector_index_task,
        extract_result=vector_index_result_candidate,
        accept_result=accept_forwarded_vector_index_result,
    ):
        return
    vector_index_result = None
    if "status" not in response:
        return
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )

    recovery_child = recovery_task_role(task) == "child"
    authoritative_recovery_task = None
    if recovery_child:
        authoritative_recovery_task = (
            get_repository_registry().task_repo.get_by_id(tid)
        )
        if authoritative_recovery_task is None:
            raise RuntimeError("recovery_result_task_missing")
        history = list(
            getattr(authoritative_recovery_task, "history", None)
            or []
        )
        proposal_meta = dict(
            getattr(
                authoritative_recovery_task,
                "last_proposal",
                None,
            )
            or {}
        )
        verification_status = dict(
            getattr(
                authoritative_recovery_task,
                "verification_status",
                None,
            )
            or {}
        )
    else:
        history = list(task.get("history", []) or [])
        proposal_meta = dict(task.get("last_proposal", {}) or {})
        verification_status = dict(
            task.get("verification_status") or {}
        )
    raw_artifacts = response.get("artifacts")
    artifacts = (
        normalize_recovery_forwarded_artifacts(
            task_id=tid,
            artifacts=raw_artifacts,
        )
        if recovery_child
        else normalize_forwarded_artifacts(
            task_id=tid,
            artifacts=(
                list(raw_artifacts)
                if isinstance(raw_artifacts, list)
                else None
            ),
        )
    )
    from agent.services.recovery_worker_result_service import (
        get_recovery_worker_result_service,
    )

    if recovery_child:
        verification_status = (
            get_recovery_worker_result_service().merge_response(
                task_id=tid,
                phase="execute",
                response=response,
                verification_status=verification_status,
            )
        )
    elif response.get("recovery_worker_result") is not None:
        raise ValueError("recovery_worker_result_unexpected")
    execution_scope = response.get("execution_scope") if isinstance(response.get("execution_scope"), dict) else None
    execution_provenance = (
        response.get("execution_provenance") if isinstance(response.get("execution_provenance"), dict) else None
    )
    review = response.get("review") if isinstance(response.get("review"), dict) else None
    assistant_request = _accept_visual_process_assistant_result(
        tid=tid,
        response=response,
        task=task,
    )
    if assistant_request is not None:
        verification_status["visual_process_assistant_request"] = assistant_request
    unsloth_completion_outbox_task_id = None
    worker_context = task.get("worker_execution_context")
    unsloth_context = (
        worker_context.get("unsloth_task")
        if isinstance(worker_context, Mapping)
        else None
    )
    unsloth_projection = None
    if (
        isinstance(unsloth_context, Mapping)
        or response.get("schema")
        == "ananta.unsloth-worker-task-result.v1"
    ):
        from agent.services.unsloth_worker_result_service import (
            get_unsloth_worker_result_projector,
        )

        unsloth_projection = get_unsloth_worker_result_projector().project(
            task_id=tid,
            task=task,
            response=response,
        )
    if unsloth_projection is not None:
        unsloth_completion_outbox_task_id = (
            str(
                unsloth_projection.pop(
                    (
                        "_unsloth_completion_"
                        "outbox_task_id"
                    ),
                    "",
                )
                or ""
            ).strip()
            or None
        )
        verification_status.update(unsloth_projection)
    knowledge_index_result = _materialize_forwarded_knowledge_index_result(
        tid=tid,
        response=response,
        task=task,
        transport_deadline=transport_deadline,
    )
    if knowledge_index_result is not None:
        verification_status["knowledge_index_job_result"] = (
            knowledge_index_result
        )
    if str(response.get("schema") or "") == "ananta.mail_task_result.v1":
        result_fields = {
            "schema",
            "job_id",
            "idempotency_key",
            "operation",
            "status",
            "reason_code",
            "retryable",
            "retry_after_ms",
            "provider",
            "result_refs",
            "counters",
            "lease_fencing_token",
        }
        framework_fields = {"handler_contract"}
        unknown_fields = set(response) - result_fields - framework_fields
        if unknown_fields:
            raise ValueError("mail_task_result_forwarding_fields_unknown")
        candidate = {field: response.get(field) for field in result_fields}
        from agent.services.mail_task_service import get_mail_task_service

        normalized_result = get_mail_task_service().validate_worker_result(
            job_id=tid,
            result=candidate,
        )
        verification_status["mail_task_result"] = normalized_result
        if not recovery_child:
            get_mail_task_service().release_lease(
                job_id=tid,
                fencing_token=int(
                    normalized_result["lease_fencing_token"]
                ),
            )
    if execution_scope:
        verification_status["execution_scope"] = dict(execution_scope)
    if execution_provenance:
        verification_status["execution_provenance"] = dict(execution_provenance)
    if artifacts is not None:
        verification_status["execution_artifacts"] = artifacts
    if review:
        verification_status["execution_review"] = dict(review)
    workflow_verification = response.get("workflow_adapter_verification")
    if isinstance(workflow_verification, dict):
        adapter_result = workflow_verification.get("workflow_adapter_task_result")
        if isinstance(adapter_result, dict):
            verification_status["workflow_adapter_task_result"] = dict(adapter_result)
            nested_result = adapter_result.get("adapter_result")
            native_verification = (
                nested_result.get("verification")
                if isinstance(nested_result, dict)
                else None
            )
            if isinstance(native_verification, dict) and isinstance(
                native_verification.get("native_node_result"), dict
            ):
                verification_status["native_node_result"] = dict(
                    native_verification["native_node_result"]
                )
    history.append(
        {
            "event_type": "execution_result",
            "status": response.get("status"),
            "prompt": task.get("description"),
            "reason": "Forwarded to " + str(task.get("assigned_agent_url")),
            "command": request_data.command
            or proposal_meta.get("command"),
            "output": response.get("output"),
            "exit_code": response.get("exit_code"),
            "backend": proposal_meta.get("backend"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "artifacts": artifacts,
            "execution_scope": execution_scope,
            "execution_provenance": execution_provenance,
            "review": review,
            "forwarded": True,
            "timestamp": time.time(),
        }
    )
    update_values = {
        "history": history,
        "last_output": response.get("output"),
        "last_exit_code": response.get("exit_code"),
        "verification_status": verification_status,
    }
    if isinstance(last_proposal, dict):
        update_values["last_proposal"] = dict(last_proposal)
    _persist_forwarded_execution_status(
        job_id=tid,
        response=response,
        status_values=update_values,
        recovery_child=recovery_child,
        authoritative_recovery_task=authoritative_recovery_task,
        vector_index_result=vector_index_result,
        accept_vector_result=accept_forwarded_vector_index_result,
        update_task_status=update_local_task_status,
        bound_knowledge_index_result=(
            knowledge_index_result
            if knowledge_index_result is not None
            and _has_governed_codecompass_binding(task)
            else None
        ),
        publish_bound_knowledge_index_result=(
            _publish_forwarded_bound_knowledge_index_result
        ),
    )
    if unsloth_completion_outbox_task_id is not None:
        from agent.services.unsloth_completion_outbox_service import (
            get_unsloth_completion_outbox_reconciler,
        )

        if not get_unsloth_completion_outbox_reconciler(
        ).reconcile_task(
            unsloth_completion_outbox_task_id
        ):
            raise RuntimeError(
                "unsloth_completion_outbox_reconciliation_failed"
            )
    if recovery_child:
        from agent.services.recovery_hub_run_evidence_service import (
            get_recovery_hub_run_evidence_service,
        )

        get_recovery_hub_run_evidence_service().accept_worker_result(
            task_id=tid,
            response=response,
            request_data=request_data,
            repositories=get_repository_registry(),
        )
    from agent.services.recovery_result_verification_service import (
        get_recovery_result_verification_service,
    )

    verification_result = (
        get_recovery_result_verification_service().verify_and_record(
            task_id=tid,
            response=response,
            artifacts=artifacts,
            publish_failure_status=False,
        )
    )
    if not recovery_child:
        return
    if not isinstance(verification_result, dict):
        raise RuntimeError("recovery_result_verification_missing")

    latest = get_repository_registry().task_repo.get_by_id(tid)
    if latest is None:
        raise RuntimeError("recovery_result_task_missing")
    final_status = (
        "completed"
        if str(verification_result.get("status") or "")
        .strip()
        .lower()
        == "passed"
        else "verification_failed"
    )
    from agent.services.recovery_dispatch_gate_service import (
        build_recovery_result_candidate,
    )

    detached = (
        latest.model_copy(deep=True)
        if callable(getattr(latest, "model_copy", None))
        else copy.deepcopy(latest)
    )
    details = dict(
        getattr(detached, "status_reason_details", None) or {}
    )
    lease = dict(details.get("recovery_dispatch_lease") or {})
    details["recovery_result_candidate"] = (
        build_recovery_result_candidate(
            task_id=tid,
            status=final_status,
            verification_record_id=str(
                verification_result.get("record_id") or ""
            ),
            lease_revision=int(lease.get("revision") or 0),
            lease_token_digest=str(
                lease.get("token_digest") or ""
            ),
            request_fingerprint=str(
                lease.get("request_fingerprint") or ""
            ),
        )
    )
    detached.status_reason_details = details
    if hasattr(detached, "updated_at"):
        detached.updated_at = time.time()
    get_repository_registry().task_repo.save(detached)


def normalize_forwarded_artifacts(*, task_id: str, artifacts: list[dict] | None) -> list[dict] | None:
    if artifacts is None:
        return None
    normalized: list[dict] = []
    for idx, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        artifact_id = str(row.get("artifact_id") or row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        path = str(row.get("path") or row.get("name") or row.get("filename") or row.get("title") or "").strip()
        if not artifact_id:
            artifact_id = f"{task_id}-artifact-{idx:03d}"
        if not kind:
            kind = "task_output"
        row["artifact_id"] = artifact_id
        row.setdefault("id", artifact_id)
        row["kind"] = kind
        if path:
            row["path"] = path
        row.setdefault("task_id", task_id)
        normalized.append(row)
    return normalized


def normalize_recovery_forwarded_artifacts(
    *,
    task_id: str,
    artifacts: object,
) -> list[dict] | None:
    """Reject unbounded or open Worker artifact claims before any Hub write."""

    if artifacts is None:
        return None
    from ananta_contracts.recovery_artifact_ingress import (
        RecoveryArtifactIngressContractError,
        validate_recovery_artifact_receipt_list,
    )

    try:
        receipts = validate_recovery_artifact_receipt_list(
            artifacts,
            task_id=task_id,
        )
    except RecoveryArtifactIngressContractError as exc:
        raise ValueError(exc.reason_code) from exc
    return normalize_forwarded_artifacts(
        task_id=task_id,
        artifacts=receipts,
    )
