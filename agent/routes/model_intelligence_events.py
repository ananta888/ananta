"""Sanitized audit and operational events for Model-Intelligence API requests.

The blueprint registers the ``after_request`` hook and passes the request view,
the application extension registry and configuration in explicitly; this module
only derives bounded, content-free event dimensions and emits them through the
injected ports (SRP/DIP). Observability never changes API results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping, MutableMapping

from flask import Response

_OPERATIONAL_STATES = {
    "submission_pending": "queued",
    "queued": "queued",
    "running": "running",
    "cancel_requested": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}


@dataclass(frozen=True)
class ModelIntelligenceRequestView:
    """The request attributes event emission is allowed to read."""

    method: str
    path: str
    endpoint: str
    view_args: Mapping[str, Any] = field(default_factory=dict)


def model_intelligence_event_context(
    response: Response,
    request_view: ModelIntelligenceRequestView,
) -> tuple[str, str, str | None, str | None]:
    """Return only bounded, non-content event dimensions for the current request."""
    path = request_view.path.rstrip("/")
    endpoint = request_view.endpoint
    if request_view.method == "POST" and path.endswith("/cancel"):
        action, resource_kind = "cancel_analysis", "job"
    elif request_view.method == "POST" and path.endswith("/jobs"):
        action, resource_kind = "submit_analysis", "job"
    elif endpoint.endswith("get_report") or path.endswith("/report"):
        action, resource_kind = "read_report", "report"
    elif "artifact" in endpoint or "/artifacts/" in path or path.endswith("/graph"):
        action, resource_kind = "read_artifact", "artifact"
    else:
        action, resource_kind = "read_job", "job"

    payload = response.get_json(silent=True) if response.is_json else None
    job_payload = payload.get("job", payload) if isinstance(payload, dict) else {}
    job_id = job_payload.get("job_id") if isinstance(job_payload, dict) else None
    state = job_payload.get("state") if isinstance(job_payload, dict) else None
    if not isinstance(job_id, str):
        candidate = (request_view.view_args or {}).get("job_id")
        job_id = candidate if isinstance(candidate, str) else None
    if not isinstance(state, str):
        state = None
    return action, resource_kind, job_id, state


def emit_sanitized_model_intelligence_events(
    response: Response,
    *,
    request_view: ModelIntelligenceRequestView,
    extensions: MutableMapping[str, Any],
    config: Mapping[str, Any],
    secret_key: object,
    logger: logging.Logger,
) -> Response:
    """Best-effort emission through injected ports; observability never changes API results."""
    from agent.services.model_analysis_task_port import HubModelAnalysisTaskSubmissionPort
    from agent.services.model_intelligence_observability import (
        HmacModelIntelligenceCorrelationService,
        ModelIntelligenceOperationalEvent,
    )
    from agent.services.model_intelligence_security_policy import (
        sanitize_model_intelligence_audit_event,
    )

    try:
        action, resource_kind, job_id, state = model_intelligence_event_context(response, request_view)
        outcome = "success" if response.status_code < 400 else "error"
        audit_fields: dict[str, object] = {
            "action": action,
            "resource_kind": resource_kind,
            "outcome": outcome,
        }
        if state:
            audit_fields["state"] = state
        if response.status_code == 403:
            audit_fields["reason_code"] = "policy_denied"
        audit_event = sanitize_model_intelligence_audit_event(
            "model_intelligence_api_request",
            audit_fields,
        )
        audit_port = extensions.get("model_intelligence_audit_event_port")
        audit_emit = getattr(audit_port, "emit", None)
        if callable(audit_emit):
            audit_emit(audit_event)

        normalized_path = request_view.path.rstrip("/")
        is_submission = request_view.method == "POST" and normalized_path.endswith("/jobs")
        is_cancellation = request_view.method == "POST" and normalized_path.endswith("/cancel")
        operational_port = extensions.get("model_intelligence_operational_event_port")
        operational_emit = getattr(operational_port, "emit", None)
        if (
            callable(operational_emit)
            and response.status_code < 400
            and job_id
            and (is_submission or is_cancellation)
        ):
            correlation_service = extensions.get("model_intelligence_correlation_service")
            if correlation_service is None:
                configured_secret = config.get("MODEL_INTELLIGENCE_CORRELATION_SECRET") or secret_key
                if configured_secret:
                    secret_bytes = (
                        configured_secret
                        if isinstance(configured_secret, bytes)
                        else str(configured_secret).encode("utf-8")
                    )
                    correlation_service = HmacModelIntelligenceCorrelationService(
                        sha256(secret_bytes).digest()
                    )
                    extensions["model_intelligence_correlation_service"] = correlation_service
            if correlation_service is not None:
                correlation = correlation_service.correlate(
                    hub_job_id=job_id,
                    worker_task_id=HubModelAnalysisTaskSubmissionPort.execution_task_id(job_id),
                )
                normalized_state = _OPERATIONAL_STATES.get(
                    state or "", "queued" if is_submission else "running"
                )
                operational_emit(
                    ModelIntelligenceOperationalEvent(
                        state=normalized_state,
                        reason_code="accepted" if is_submission else "cancelled",
                        correlation=correlation,
                        duration_seconds=0,
                    )
                )
    except Exception:
        logger.debug(
            "model-intelligence event emission failed",
            exc_info=True,
        )
    return response


__all__ = [
    "ModelIntelligenceRequestView",
    "emit_sanitized_model_intelligence_events",
    "model_intelligence_event_context",
]
