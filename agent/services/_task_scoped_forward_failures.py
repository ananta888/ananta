"""Worker-forward outcome recording and forwarding-failure mapping.

Split out of ``_task_scoped_forwarding`` (SRP): recording forward
success/failure outcomes, translating worker HTTP errors, the explicit 404
Hub-fallback switch and the completion-projection-pending response.
``_task_scoped_forwarding`` re-exports every name; patchable collaborators are
resolved through that facade at call time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable

from flask import current_app

from agent.common.errors import WorkerForwardingError
from agent.services.worker_forward_transport import WorkerForwardDeadlineExceeded, WorkerForwardTransportError

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def _facade():
    """Resolve patchable collaborators through the public ``_task_scoped_forwarding`` entry point."""
    import agent.services._task_scoped_forwarding as facade_module

    return facade_module


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
        recorder = _facade().get_worker_forward_outcome_recorder()
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
        recorder = _facade().get_worker_forward_outcome_recorder()
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
        _facade()._record_forwarded_worker_failure(
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
