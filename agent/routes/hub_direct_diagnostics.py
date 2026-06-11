"""HDE-022: diagnostics and admin API for hub-direct execution and
custom tools.

Read endpoints (any authenticated user) expose the direct-execution
config, the registry snapshot (static + dynamic), recent direct
decisions and the reuse metrics. They never return secrets, script
bodies or command templates — the dynamic snapshot only carries name,
status, version, risk and usage counters.

Mutating endpoints (proposal validation, approval, activation, disable,
rollback) require admin privileges (HDE-DD-004: only the hub promotes).
Proposal *creation* is open to authenticated users/LLM surfaces — a
proposal is inert until an admin drives it through the promotion
lifecycle.

Operator-TUI integration is a documented TODO
(``docs/architecture/hub-direct-execution.md``).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from agent.auth import admin_required, check_auth

hub_direct_diagnostics_bp = Blueprint("hub_direct_diagnostics", __name__)


def _agent_cfg() -> dict:
    return current_app.config.get("AGENT_CONFIG", {}) or {}


@hub_direct_diagnostics_bp.get("/api/diagnostics/hub-direct/config")
@check_auth
def get_hub_direct_config():
    cfg = _agent_cfg().get("hub_direct_execution")
    return jsonify({"hub_direct_execution": dict(cfg) if isinstance(cfg, dict) else {}})


@hub_direct_diagnostics_bp.get("/api/diagnostics/hub-direct/metrics")
@check_auth
def get_hub_direct_metrics():
    from agent.services.task_execution_metrics import hub_direct_metrics_snapshot, last_hub_direct_decisions

    return jsonify({"metrics": hub_direct_metrics_snapshot(), "recent_decisions": last_hub_direct_decisions()})


@hub_direct_diagnostics_bp.get("/api/diagnostics/hub-direct/registry")
@check_auth
def get_hub_direct_registry():
    from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    return jsonify(
        {
            "static": get_ananta_tool_registry_service().registry_snapshot(),
            "dynamic": get_dynamic_tool_registry_service().registry_snapshot(),
        }
    )


@hub_direct_diagnostics_bp.get("/api/custom-tools")
@check_auth
def list_custom_tools():
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    return jsonify(get_dynamic_tool_registry_service().registry_snapshot())


@hub_direct_diagnostics_bp.post("/api/custom-tools/proposals")
@check_auth
def create_custom_tool_proposal():
    from agent.services.custom_tool_proposal_service import get_custom_tool_proposal_service

    payload = request.get_json(silent=True) or {}
    try:
        proposal = get_custom_tool_proposal_service().create_proposal(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {
            "proposal_digest": proposal.get("proposal_digest"),
            "name": proposal.get("name"),
            "status": proposal.get("status"),
        }
    ), 201


@hub_direct_diagnostics_bp.get("/api/custom-tools/proposals")
@check_auth
def list_custom_tool_proposals():
    from agent.services.custom_tool_proposal_service import get_custom_tool_proposal_service

    status = str(request.args.get("status") or "").strip() or None
    rows = [
        {
            "proposal_digest": row.get("proposal_digest"),
            "name": row.get("name"),
            "status": row.get("status"),
            "approval_status": row.get("approval_status"),
            "risk_class": row.get("risk_class"),
            "validation_report_ref": row.get("validation_report_ref"),
        }
        for row in get_custom_tool_proposal_service().list_proposals(status=status)
    ]
    return jsonify({"proposals": rows})


@hub_direct_diagnostics_bp.post("/api/custom-tools/proposals/<digest>/validate")
@admin_required
def validate_custom_tool_proposal(digest: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionError, get_custom_tool_promotion_service

    try:
        proposal = get_custom_tool_promotion_service().validate(digest)
    except CustomToolPromotionError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"status": proposal.get("status"), "validation_report_ref": proposal.get("validation_report_ref")})


@hub_direct_diagnostics_bp.post("/api/custom-tools/proposals/<digest>/request-approval")
@admin_required
def request_custom_tool_approval(digest: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionError, get_custom_tool_promotion_service

    try:
        proposal = get_custom_tool_promotion_service().request_approval(digest, agent_cfg=_agent_cfg())
    except CustomToolPromotionError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"status": proposal.get("status"), "approval_request_id": proposal.get("approval_request_id")})


@hub_direct_diagnostics_bp.post("/api/custom-tools/proposals/<digest>/activate")
@admin_required
def activate_custom_tool(digest: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionError, get_custom_tool_promotion_service

    try:
        get_custom_tool_promotion_service().refresh_approval(digest)
    except CustomToolPromotionError:
        pass
    try:
        record = get_custom_tool_promotion_service().activate(digest)
    except CustomToolPromotionError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"name": record.get("name"), "version": record.get("version"), "status": record.get("status")})


@hub_direct_diagnostics_bp.post("/api/custom-tools/<name>/disable")
@admin_required
def disable_custom_tool(name: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionService
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    record = CustomToolPromotionService(registry=get_dynamic_tool_registry_service()).disable(name)
    if record is None:
        return jsonify({"error": "unknown_custom_tool"}), 404
    return jsonify({"name": record.get("name"), "status": record.get("status")})


@hub_direct_diagnostics_bp.post("/api/custom-tools/<name>/enable")
@admin_required
def enable_custom_tool(name: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionService
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    try:
        record = CustomToolPromotionService(registry=get_dynamic_tool_registry_service()).reactivate(name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    if record is None:
        return jsonify({"error": "unknown_custom_tool"}), 404
    return jsonify({"name": record.get("name"), "status": record.get("status")})


@hub_direct_diagnostics_bp.post("/api/custom-tools/<name>/rollback")
@admin_required
def rollback_custom_tool(name: str):
    from agent.services.custom_tool_promotion_service import CustomToolPromotionService
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    payload = request.get_json(silent=True) or {}
    try:
        record = CustomToolPromotionService(registry=get_dynamic_tool_registry_service()).rollback(name, int(payload.get("version") or 0))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"name": record.get("name"), "version": record.get("version"), "status": record.get("status")})
