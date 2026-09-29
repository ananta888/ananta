"""CodeCompass/knowledge-index binding checks and worker-dispatch admission.

Split out of ``_task_scoped_forwarding`` (SRP): classifying the public-v1 /
governed-v2 knowledge-index job bindings of a forwarded task, detecting
self-forwarding to the local runtime, authorizing the governed worker dispatch
and deriving its execute deadline. ``_task_scoped_forwarding`` re-exports every
name; patchable collaborators are resolved through that facade at call time.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from flask import current_app

from agent.common.errors import WorkerForwardingError
from agent.services.worker_forward_transport import WorkerTransportDeadline


def _facade():
    """Resolve patchable collaborators through the public ``_task_scoped_forwarding`` entry point."""
    import agent.services._task_scoped_forwarding as facade_module

    return facade_module


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
    _facade()._authorize_codecompass_worker_dispatch(
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
            _facade()._governed_source_control_index_job_service()
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


def _attach_codecompass_capability(payload: dict[str, Any], *, task: dict[str, Any], worker_url: str) -> None:
    """Delegated CodeCompass access (WCRB-009): the Hub's own signed capability for this task and worker.

    A capability that arrived with the request is never forwarded; without delegated access none is sent.
    """
    payload.pop("codecompass_capability", None)
    from agent.cli_backends.tool_loop import get_tool_loop_config
    from agent.services.codecompass_task_capability import issue_task_capability, uses_delegation

    if not uses_delegation(get_tool_loop_config()):
        return
    capability = issue_task_capability(task, audience=worker_url)
    if capability is not None:
        payload["codecompass_capability"] = capability
