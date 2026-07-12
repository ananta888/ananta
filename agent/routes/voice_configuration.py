from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_user_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.voice_configuration_service import get_voice_configuration_service
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal

voice_configuration_bp = Blueprint("voice_configuration", __name__)


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return VoicePrincipal(tenant_id=tenant_id, subject=subject)


def _error(exc: VoiceGovernanceError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": False}},
    )


@voice_configuration_bp.get("/v1/voice/configuration/schema")
@check_user_auth
def configuration_schema():
    return api_response(data={"schema": get_voice_configuration_service().schema()})


@voice_configuration_bp.get("/v1/voice/configuration")
@check_user_auth
def get_configuration():
    try:
        result = get_voice_configuration_service().resolve(
            _principal(),
            legacy_global=current_app.config.get("AGENT_CONFIG", {}) or {},
            profile_id=request.args.get("profile_id"),
            session_id=request.args.get("session_id"),
        )
        return api_response(data={"configuration": result.as_dict()})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_configuration_bp.put("/v1/voice/configuration")
@check_user_auth
def put_configuration():
    try:
        body: Any = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise VoiceGovernanceError(
                code="voice_configuration.invalid_json",
                message="JSON object body is required",
                status_code=400,
            )
        scope = str(body.get("scope") or "")
        if scope == "global" and not bool(getattr(g, "is_admin", False)):
            raise VoiceGovernanceError(
                code="voice_configuration.admin_required",
                message="global voice configuration requires admin role",
                status_code=403,
            )
        key = str(request.headers.get("Idempotency-Key") or "").strip()
        result = get_voice_configuration_service().put_delta(
            _principal(),
            scope=scope,
            scope_id=body.get("scope_id"),
            delta=body.get("delta") or {},
            expected_version=body.get("expected_version"),
            idempotency_key=key,
        )
        log_audit(
            "voice_configuration_updated",
            {
                "scope": result["scope"],
                "scope_id": result["scope_id"],
                "version": result["version"],
                "field_names": sorted(result["delta"]),
                "idempotent_replay": result["idempotent_replay"],
            },
        )
        return api_response(data={"configuration": result})
    except VoiceGovernanceError as exc:
        return _error(exc)
