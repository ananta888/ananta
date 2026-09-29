"""Voice exposure-policy guard and privacy state, backed by the Voice route collaborators."""

from __future__ import annotations

import uuid

from flask import (
    current_app,
    g,
    request,
)

from agent.common.errors import api_response
from agent.routes.voice_request_support import _audit_identity
from agent.routes.voice_route_dependencies import voice_route_dependencies


def _voice_privacy_state() -> dict:
    # Raw audio persistence is intentionally fail-closed until explicit storage wiring exists.
    return {
        "store_audio_requested": bool(voice_route_dependencies().store_audio_enabled()),
        "store_audio_effective": False,
        "effective_audio_retention": "none",
        "policy_hint": "raw_audio_persistence_not_wired",
        "raw_audio_persisted": False,
        "raw_audio_persisted_after_request": False,
        "transient_request_spooling": True,
    }


def _enforce_voice_policy(operation: str):
    dependencies = voice_route_dependencies()
    is_agent_auth = bool(getattr(g, "auth_payload", None))
    is_user_auth = bool(getattr(g, "user", None))
    decision = dependencies.get_exposure_policy_service().evaluate_voice_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=is_agent_auth,
        is_user_auth=is_user_auth,
        is_admin=bool(getattr(g, "is_admin", False)),
        operation=operation,
    )
    if decision.allowed:
        return None, decision.policy
    if decision.policy.get("emit_audit_events", True):
        actor, tenant_id = _audit_identity()
        dependencies.log_audit(
            "voice_access_blocked",
            {
                "actor": actor,
                "tenant_id": tenant_id,
                "reason": decision.reason,
                "auth_source": decision.auth_source,
                "operation": operation,
                "policy_decision": "denied",
                "request_id": str(request.headers.get("X-Request-ID") or f"voice-policy-{uuid.uuid4().hex}"),
            },
        )
    return (
        api_response(
            status="error",
            code=403,
            data={"error": {"code": "policy_denied", "message": decision.reason, "retriable": False}},
        ),
        decision.policy,
    )
