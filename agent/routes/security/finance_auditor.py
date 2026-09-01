"""Authenticated, read-only finance-auditor API."""

from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import admin_required
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.finance_auditor.config import MonetativeAuditorConfig, ZieglerAuditorConfig
from agent.services.finance_auditor.models import ZieglerAuditInput
from agent.services.finance_auditor.service import ZieglerAuditorService

finance_auditor_bp = Blueprint(
    "finance_auditor",
    __name__,
    url_prefix="/api/security/finance-auditor",
)


@finance_auditor_bp.post("/ziegler")
@admin_required
def audit_ziegler_claim():
    try:
        config = ZieglerAuditorConfig.from_agent_config(current_app.config.get("AGENT_CONFIG"))
        monetative_config = MonetativeAuditorConfig.from_agent_config(current_app.config.get("AGENT_CONFIG"))
    except ValueError as exc:
        return api_response(status="error", message=str(exc), data={"read_only": True}, code=409)
    if not config.enabled:
        return api_response(status="error", message="ziegler_auditor_disabled", data={"read_only": True}, code=409)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_response(status="error", message="ziegler_audit_payload_invalid", data={"read_only": True}, code=422)
    try:
        normalized_payload = dict(payload)
        normalized_payload.setdefault("requested_tone", config.tone.value)
        audit_input = ZieglerAuditInput.from_mapping(normalized_payload)
        result = ZieglerAuditorService(
            config,
            monetative_config=monetative_config,
        ).audit(audit_input)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), data={"read_only": True}, code=422)
    log_audit(
        "ziegler_finance_audit_completed",
        {
            "asset_type": audit_input.asset_type.value,
            "classification": list(result.classification),
            "read_only": True,
            "source_count": len(audit_input.optional_sources),
        },
    )
    return api_response(data=result.as_dict())


__all__ = ["finance_auditor_bp"]
