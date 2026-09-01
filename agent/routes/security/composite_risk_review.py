"""Explicit, headless API for advisory Composite Risk Review."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.composite_risk_review_contract import COMPOSITE_RISK_REVIEW_WARNING
from agent.config import settings
from agent.services.composite_risk_review_service import get_composite_risk_review_service

composite_risk_review_bp = Blueprint(
    "composite_risk_review",
    __name__,
    url_prefix="/api/security/composite-risk-review",
)


def _flag(name: str, default: bool) -> bool:
    value = current_app.config.get(name, default)
    return value is True


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("composite_risk_review_payload_invalid")
    allowed = {"explicit_request", "goal", "tasks", "artifacts_metadata", "audit_events"}
    if set(value) - allowed:
        raise ValueError("composite_risk_review_unknown_field")
    if value.get("goal") is not None and not isinstance(value.get("goal"), (str, dict)):
        raise ValueError("composite_risk_review_goal_invalid")
    limits = {"tasks": 1000, "artifacts_metadata": 2000, "audit_events": 2000}
    for field, limit in limits.items():
        if value.get(field) is not None and not isinstance(value.get(field), list):
            raise ValueError(f"composite_risk_review_{field}_invalid")
        if isinstance(value.get(field), list) and len(value[field]) > limit:
            raise ValueError(f"composite_risk_review_{field}_too_large")
    return value


@composite_risk_review_bp.post("")
@admin_required
def review_composite_risk():
    if not _flag("COMPOSITE_RISK_REVIEW_ENABLED", settings.composite_risk_review_enabled):
        return api_response(
            status="error",
            message="composite_risk_review_disabled",
            data={"warning_text": COMPOSITE_RISK_REVIEW_WARNING},
            code=409,
        )
    try:
        payload = _payload()
    except ValueError as exc:
        return api_response(
            status="error",
            message=str(exc),
            data={"warning_text": COMPOSITE_RISK_REVIEW_WARNING},
            code=422,
        )
    explicit_only = _flag(
        "COMPOSITE_RISK_REVIEW_EXPLICIT_ONLY",
        settings.composite_risk_review_explicit_only,
    )
    if explicit_only and payload.get("explicit_request") is not True:
        return api_response(
            status="error",
            message="composite_risk_review_explicit_request_required",
            data={"warning_text": COMPOSITE_RISK_REVIEW_WARNING},
            code=422,
        )

    result = get_composite_risk_review_service().review(
        goal=payload.get("goal"),
        tasks=payload.get("tasks"),
        artifacts_metadata=payload.get("artifacts_metadata"),
        audit_events=payload.get("audit_events"),
    )
    log_audit(
        "composite_risk_review_completed",
        {
            "risk_level": result["risk_level"],
            "indicator_ids": [item["id"] for item in result["indicators"]],
            "task_count": len(payload.get("tasks") or []),
            "artifact_count": len(payload.get("artifacts_metadata") or []),
            "review_only": True,
        },
    )
    return api_response(data=result)


__all__ = ["composite_risk_review_bp"]
