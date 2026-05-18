from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.services.agent_template_registry import get_agent_template_registry
from agent.services.rag_policy_service import SENSITIVITY_CLASSES

admin_agent_templates_bp = Blueprint("admin_agent_templates", __name__)

_VALID_SCOPE_MODES = {"full", "selective", "none"}


def _validate_context_policy(data: dict) -> list[str]:
    errors = []
    if "scope_mode" in data and data["scope_mode"] not in _VALID_SCOPE_MODES:
        errors.append(f"scope_mode must be one of {sorted(_VALID_SCOPE_MODES)}, got '{data['scope_mode']}'")
    if "max_files" in data:
        try:
            v = int(data["max_files"])
            if v <= 0:
                errors.append("max_files must be > 0")
        except (TypeError, ValueError):
            errors.append("max_files must be an integer > 0")
    if "sensitivity_ceiling" in data and data["sensitivity_ceiling"] not in SENSITIVITY_CLASSES:
        errors.append(
            f"sensitivity_ceiling must be one of {sorted(SENSITIVITY_CLASSES)}, got '{data['sensitivity_ceiling']}'"
        )
    return errors


@admin_agent_templates_bp.route("/admin/agent-templates", methods=["GET"])
def list_agent_templates():
    registry = get_agent_template_registry()
    return jsonify({"templates": registry.list_templates()}), 200


@admin_agent_templates_bp.route("/admin/agent-templates/<template_id>/context-policy", methods=["POST"])
def set_agent_template_context_policy(template_id: str):
    data = request.get_json(force=True, silent=True) or {}
    errors = _validate_context_policy(data)
    if errors:
        return jsonify({"error": "invalid_context_policy", "details": errors}), 400
    get_agent_template_registry().register_override(template_id, data)
    return jsonify({"template_id": template_id, "context_policy": data}), 200


@admin_agent_templates_bp.route("/admin/agent-templates/<template_id>/context-policy", methods=["DELETE"])
def delete_agent_template_context_policy(template_id: str):
    get_agent_template_registry().clear_override(template_id)
    return jsonify({"template_id": template_id, "status": "override_removed"}), 200
