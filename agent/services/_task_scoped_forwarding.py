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
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable, Protocol

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import WorkerForwardingError
from agent.config import settings

# The names below are re-exported: callers, the extracted sibling modules
# (which resolve patchable collaborators through this facade at call time)
# and tests import or monkeypatch them on this module.
from agent.services._task_scoped_codecompass_dispatch import (  # noqa: F401
    _attach_codecompass_capability,
    _authorize_codecompass_worker_dispatch,
    _codecompass_execute_deadline,
    _governed_source_control_index_job_service,
    _has_governed_codecompass_binding,
    _has_public_codecompass_v1_binding,
    _is_codecompass_index_task,
    _permanent_codecompass_forwarding_error,
    _prepare_codecompass_worker_dispatch,
    _requires_governed_codecompass_transport,
    _urls_resolve_same_runtime,
)
from agent.services._task_scoped_forward_failures import (  # noqa: F401
    _completion_projection_pending_response,
    _handle_forwarding_failure,
    _is_completion_projection_pending,
    _raise_forwarded_worker_http_error,
    _record_forwarded_worker_failure,
    _record_forwarded_worker_success,
    _worker_404_hub_fallback_enabled,
)
from agent.services._task_scoped_forwarded_proposal import persist_forwarded_proposal  # noqa: F401
from agent.services._task_scoped_forwarded_result_acceptance import (  # noqa: F401
    _FORWARDED_HANDLER_FRAMEWORK_FIELDS,
    _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS,
    _VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS,
    _accept_codecompass_layer_result,
    _accept_local_runtime_capability_result,
    _accept_visual_process_assistant_result,
    _get_visual_process_assistant_service,
)
from agent.services._task_scoped_knowledge_index_forwarding import (  # noqa: F401
    _governed_knowledge_index_retry_expiries,
    _invoke_governed_knowledge_index_forwarder,
    _is_typed_knowledge_index_result_pending,
    _materialize_forwarded_knowledge_index_result,
    _publish_forwarded_bound_knowledge_index_result,
    _require_governed_knowledge_index_retry_window,
)
from agent.services._vector_index_result_forwarding import (
    accept_bound_forwarded_vector_index_result,
    accept_forwarded_vector_index_result,
    is_authoritative_vector_index_task,
    vector_index_result_candidate,
)
from agent.services._vector_index_result_forwarding import (
    persist_forwarded_execution_status as _persist_forwarded_execution_status,
)
from agent.services.forwarded_artifact_normalization import (
    normalize_forwarded_artifacts as normalize_forwarded_artifacts,
)
from agent.services.forwarded_artifact_normalization import (
    normalize_recovery_forwarded_artifacts as normalize_recovery_forwarded_artifacts,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services  # noqa: F401 - patch seam for extracted modules
from agent.services.task_runtime_service import update_local_task_status
from agent.services.worker_forward_outcome import (  # noqa: F401 - patch seam for _task_scoped_forward_failures
    get_worker_forward_outcome_recorder,
)
from agent.services.worker_forward_transport import (
    DeadlineAwareWorkerForwarder,
    WorkerTransportDeadline,
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


def _with_hub_context_window(endpoint: str, payload: dict) -> dict:
    """A step forwarded to a worker carries the window the Hub sizes tasks for (hub-owned, like the autopilot's
    requests). Requests under a dispatch lease already carry it and are fingerprinted: left untouched."""
    if not str(endpoint or "").endswith(("/step/propose", "/step/execute")) or not isinstance(payload, dict):
        return payload
    if payload.get("context_window") or payload.get("dispatch_lease_token"):
        return payload
    try:
        from agent.context_profile import hub_assignment

        return {**payload, "context_window": hub_assignment()}
    except Exception:  # noqa: BLE001 -- without it the worker uses its own configuration
        return payload


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

    payload = _with_hub_context_window(endpoint, payload)
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
    _attach_codecompass_capability(payload, task=task, worker_url=str(worker_url))
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


def persist_forwarded_execution(
    *,
    tid: str,
    response: dict,
    task: dict,
    request_data,
    last_proposal: dict | None = None,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> None:
    from agent.services.pi_result_forwarding import validate_forwarded_pi_result

    validate_forwarded_pi_result(
        task_id=tid, dispatched_task=task, response=response,
        load_task=get_repository_registry().task_repo.get_by_id,
    )
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
    verification_status.update(
        _accept_local_runtime_capability_result(task=task, response=response)
    )
    verification_status.update(
        _accept_codecompass_layer_result(tid=tid, task=task, response=response)
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
