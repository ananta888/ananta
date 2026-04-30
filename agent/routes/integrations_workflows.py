from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.redaction import redact
from agent.services.workflow_auth import verify_callback_signature

integrations_workflows_bp = Blueprint("integrations_workflows", __name__)


@integrations_workflows_bp.route("/api/integrations/workflows/callback", methods=["POST"])
def workflow_callback() -> tuple:
    payload = request.get_json(silent=True) or {}
    correlation_id = str(payload.get("correlation_id") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    if not correlation_id or not provider:
        return api_response(status="error", message="missing_correlation_or_provider", code=400)

    signature = request.headers.get("X-Workflow-Signature", "")
    timestamp = request.headers.get("X-Workflow-Timestamp", "")
    secret = str(current_app.config.get("WORKFLOW_CALLBACK_SECRET") or "")
    if not secret:
        return api_response(status="error", message="workflow_callback_secret_missing", code=412)

    known = current_app.config.get("WORKFLOW_KNOWN_CORRELATIONS")
    if isinstance(known, (set, list, tuple)) and correlation_id not in set(str(v) for v in known):
        return api_response(status="error", message="unknown_correlation_id", code=404)

    if not verify_callback_signature(
        secret=secret,
        correlation_id=correlation_id,
        provider=provider,
        timestamp=timestamp,
        signature=signature,
    ):
        log_audit("workflow_callback_rejected", {"provider": provider, "correlation_id": correlation_id})
        return api_response(status="error", message="invalid_signature", code=401)

    log_audit("workflow_callback_received", {
        "provider": provider,
        "correlation_id": correlation_id,
        "task_id": payload.get("task_id"),
        "goal_id": payload.get("goal_id"),
        "trace_id": payload.get("trace_id"),
    })
    results = current_app.config.setdefault("WORKFLOW_CALLBACK_RESULTS", [])
    if isinstance(results, list):
        results.append(
            {
                "provider": provider,
                "correlation_id": correlation_id,
                "task_id": payload.get("task_id"),
                "goal_id": payload.get("goal_id"),
                "trace_id": payload.get("trace_id"),
                "result": redact(payload),
            }
        )
    return api_response(data={"accepted": True, "provider": provider, "correlation_id": correlation_id})
