"""VisionDecision hub routes: typed image decisions behind task policy, confirmations and escalations.

``POST /v1/vision/decision`` (JSON ``task``, ``images`` as ``data:image/png|jpeg|webp;base64`` URIs, optional
``text``; optional header ``X-Ananta-Deadline-Seconds``) runs the task's schema through the VisionDecision
specialist, the hub gate and :class:`VisionTaskPolicy`; uncertain fields are escalated, never taken over.
With ``VISION_DECISION_ENABLED`` off every route answers ``normal_path`` without reading further
configuration or contacting the service.

``POST /v1/vision/decision/confirm`` (``confirmation_id``, ``action_type``, ``confirmed``) and
``POST /v1/vision/decision/escalations/resolve`` (``escalation_id``, ``task``, ``field``, ``value``) consume
short-lived, single-use ids bound to tenant, subject and the concrete action or field.

Auth (``check_auth``) and the ``exposure_policy.vision_decision`` policy run before anything else and
again right before an action executes. A decision value never grants a permission. Audit events carry
metadata only (field names, actions, rules, scores, error codes, usage, timings).
"""
from __future__ import annotations

import os
import uuid

from flask import Blueprint, current_app, g, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.vision_decision_escalation_executor import RESOLVE_ROUTE
from agent.services.vision_decision_hub_service import (
    CONFIRM_ROUTE,
    DECISION_ROUTE,
    VisionRequestError,
    confirm_vision_action,
    decode_data_uri_images,
    evaluate_vision_access,
    get_vision_hub_runtime,
    max_request_bytes,
    resolve_escalation,
    run_vision_decision,
)
from agent.services.vision_decision_provider import MAX_TEXT_CHARS, VisionDecisionConfigurationError
from agent.services.voice_governance_domain import VoicePrincipal

vision_decision_bp = Blueprint("vision_decision", __name__)
_SMALL_BODY_BYTES = 8 * 1024


def _enabled_flag() -> bool:
    # The feature flag is the only variable read while the feature is off.
    return str(os.environ.get("VISION_DECISION_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


@vision_decision_bp.before_request
def _bound_vision_decision_body() -> None:
    request.max_content_length = _SMALL_BODY_BYTES
    if request.endpoint != "vision_decision.vision_decision" or not _enabled_flag():
        return
    try:
        runtime = get_vision_hub_runtime()
    except VisionDecisionConfigurationError:
        return  # the route answers 503; keep the small limit
    if runtime is not None:
        request.max_content_length = max_request_bytes(runtime.provider.config)


@vision_decision_bp.errorhandler(RequestEntityTooLarge)
def _vision_decision_too_large(_exc: RequestEntityTooLarge):
    return _error(413, "vision_decision.body_too_large", "request body exceeds the vision decision limits")


def _error(code: int, error_code: str, message: str):
    return api_response(status="error", code=code, data={"error": {"code": error_code, "message": message}})


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return VoicePrincipal(tenant_id=tenant_id[:128], subject=subject[:128])


def _enforce(operation: str):
    decision = evaluate_vision_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=bool(getattr(g, "auth_payload", None)),
        is_user_auth=bool(getattr(g, "user", None)),
        is_admin=bool(getattr(g, "is_admin", False)),
        operation=operation,
    )
    if decision.allowed:
        return None
    if decision.policy.get("emit_audit_events", True):
        principal = _principal()
        log_audit(
            "vision_decision_access_blocked",
            {
                "actor": principal.subject or "authenticated",
                "tenant_id": principal.tenant_id,
                "reason": decision.reason,
                "auth_source": decision.auth_source,
                "operation": operation,
                "policy_decision": "denied",
            },
        )
    return api_response(
        status="error",
        code=403,
        data={"error": {"code": "policy_denied", "message": decision.reason, "retriable": False}},
    )


def _authorizer(operation: str):
    def authorize() -> bool:
        return _enforce(operation) is None

    return authorize


def _runtime_or_response():
    """``(runtime, None)``; ``(None, disabled answer)``; ``(None, 503)`` when misconfigured."""
    if not _enabled_flag():
        return None, api_response(
            data={
                "enabled": False,
                "hub_action": "normal_path",
                "error_code": "vision_decision_disabled",
                "grants_permission": False,
            }
        )
    try:
        runtime = get_vision_hub_runtime()
    except VisionDecisionConfigurationError:
        # The message names no variable value and no key material.
        return None, _error(503, "vision_decision.misconfigured", "vision decision provider is misconfigured")
    if runtime is None:
        return None, _error(503, "vision_decision.misconfigured", "vision decision provider is misconfigured")
    return runtime, None


def _json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _deadline_seconds() -> float | None:
    raw = request.headers.get("X-Ananta-Deadline-Seconds")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise VisionRequestError("vision_decision.invalid_deadline", "deadline must be numeric") from exc
    if value != value:
        raise VisionRequestError("vision_decision.invalid_deadline", "deadline must be numeric")
    return max(0.1, min(value, 300.0))


def _audit(event: str, principal: VoicePrincipal, endpoint: str, operation: str, allowed: bool, details: dict) -> str:
    audit_id = f"audit-vision-{uuid.uuid4()}"
    log_audit(
        event,
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": operation,
            "policy_decision": "allowed" if allowed else "denied",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": endpoint,
            **details,
        },
    )
    return audit_id


@vision_decision_bp.route(DECISION_ROUTE, methods=["POST"])
@check_auth
def vision_decision():
    blocked = _enforce("decide")
    if blocked:
        return blocked
    runtime, response = _runtime_or_response()
    if runtime is None:
        return response
    body = _json_body()
    if body is None:
        return _error(400, "validation.invalid_json", "JSON object body is required")
    task_id = body.get("task")
    text = body.get("text", "")
    if not isinstance(task_id, str) or not task_id.strip():
        return _error(422, "vision_decision.task_required", "task is required")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        return _error(422, "vision_decision.invalid_text", f"text must be a string of at most {MAX_TEXT_CHARS} characters")
    config = runtime.provider.config
    principal = _principal()
    try:
        deadline = _deadline_seconds()
        images = decode_data_uri_images(body.get("images"), max_media=config.max_media, max_image_bytes=config.max_image_bytes)
        result = run_vision_decision(
            runtime,
            task_id=task_id.strip(),
            images=images,
            text=text,
            principal=principal,
            authorize=_authorizer("decide"),
            deadline_seconds=deadline,
        )
    except VisionRequestError as exc:
        return _error(exc.status_code, exc.code, str(exc))
    audit_id = _audit(
        "vision_decision",
        principal,
        DECISION_ROUTE,
        "decide",
        True,
        {"decision": result.audit, "image_bytes": sum(len(item) for item in images)},
    )
    data = {**result.response, "audit_id": audit_id}
    if result.status_code != 200:
        return api_response(
            status="error",
            code=result.status_code,
            data={**data, "error": {"code": f"vision_decision.{data.get('error_code')}", "message": "image rejected before upload"}},
        )
    return api_response(data=data)


@vision_decision_bp.route(CONFIRM_ROUTE, methods=["POST"])
@check_auth
def confirm_vision_decision():
    """Explicit confirmation of a pending ``confirm`` action; the only way such an action runs."""
    blocked = _enforce("confirm")
    if blocked:
        return blocked
    runtime, response = _runtime_or_response()
    if runtime is None:
        return response
    body = _json_body()
    if body is None:
        return _error(400, "validation.invalid_json", "JSON object body is required")
    confirmation_id, action_type, confirmed = body.get("confirmation_id"), body.get("action_type"), body.get("confirmed")
    if not isinstance(confirmation_id, str) or not confirmation_id.strip() or len(confirmation_id) > 128:
        return _error(422, "validation.confirmation_id", "confirmation_id is required")
    if not isinstance(action_type, str) or not action_type.strip() or len(action_type) > 128:
        return _error(422, "validation.action_type", "action_type is required")
    if not isinstance(confirmed, bool):
        # Only a literal boolean counts; anything else is neither a confirmation nor a rejection.
        return _error(422, "validation.confirmed", "confirmed must be true or false")
    principal = _principal()
    execution = confirm_vision_action(
        runtime,
        confirmation_id.strip(),
        principal=principal,
        action_type=action_type.strip(),
        confirmed=confirmed,
        authorize=_authorizer("confirm"),
    )
    audit_id = _audit(
        "vision_decision_confirmation",
        principal,
        CONFIRM_ROUTE,
        "confirm",
        execution.executed,
        {"confirmed": confirmed, "execution": {"status": "executed" if execution.executed else "denied", "error_code": execution.error_code}},
    )
    data = {"execution": execution.as_response(), "audit_id": audit_id, "grants_permission": False}
    if execution.executed or execution.error_code == "confirmation.rejected":
        return api_response(data=data)
    return api_response(
        status="error",
        code=410 if execution.error_code == "confirmation.expired" else 403,
        data={**data, "error": {"code": execution.error_code, "message": "vision confirmation denied", "retriable": False}},
    )


@vision_decision_bp.route(RESOLVE_ROUTE, methods=["POST"])
@check_auth
def resolve_vision_escalation():
    """A human answers an escalated field; single use, bound to principal, task and field."""
    blocked = _enforce("resolve")
    if blocked:
        return blocked
    runtime, response = _runtime_or_response()
    if runtime is None:
        return response
    body = _json_body()
    if body is None:
        return _error(400, "validation.invalid_json", "JSON object body is required")
    escalation_id, task_id, field_name = body.get("escalation_id"), body.get("task"), body.get("field")
    for name, value in (("escalation_id", escalation_id), ("task", task_id), ("field", field_name)):
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            return _error(422, f"validation.{name}", f"{name} is required")
    if "value" not in body:
        return _error(422, "validation.value", "value is required")
    principal = _principal()
    result = resolve_escalation(
        runtime,
        escalation_id.strip(),
        task_id=task_id.strip(),
        field_name=field_name.strip(),
        value=body["value"],
        principal=principal,
        authorize=_authorizer("resolve"),
    )
    audit_id = _audit(
        "vision_decision_escalation_resolved",
        principal,
        RESOLVE_ROUTE,
        "resolve",
        result.status_code == 200,
        {"resolution": result.audit},
    )
    data = {**result.response, "audit_id": audit_id}
    if result.status_code == 200:
        return api_response(data=data)
    return api_response(
        status="error",
        code=result.status_code,
        data={**data, "error": {"code": data.get("error_code"), "message": "vision escalation resolution denied", "retriable": False}},
    )
