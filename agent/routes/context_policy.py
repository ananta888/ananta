from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import admin_required, check_auth
from agent.services.context_access_policy_service import ContextAccessPolicyService
from ananta_contracts.context_access_policy import ContextAccessPolicy, ContextAccessRule
from agent.routes.source_control_access import authorize_route_request
from agent.services.source_control_access_policy import SourceControlAction

context_policy_bp = Blueprint("context_policy", __name__, url_prefix="/api/context-policy")
policy_service = ContextAccessPolicyService()


@context_policy_bp.before_request
@check_auth
def _authorize_context_policy_surface():
    endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
    policy_id = str((request.view_args or {}).get("policy_id") or "").strip()
    resource = (
        policy_service.get_latest_policy(policy_id)
        if policy_id
        else {}
    )
    return authorize_route_request(
        action=SourceControlAction.policy,
        resource_kind="context_policy",
        resource=resource,
        object_id=policy_id or endpoint,
    )


def _error(reason_code: str, status_code: int) -> tuple[Any, int]:
    return (
        jsonify(
            {
                "status": "error",
                "reason_code": reason_code,
                "message": reason_code,
            }
        ),
        status_code,
    )


def _payload() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("context_policy_payload_required")
    return data


def _policy_from_payload(data: dict[str, Any], *, temporary: bool) -> ContextAccessPolicy:
    rules_payload = data.get("rules", [])
    if not isinstance(rules_payload, list):
        raise ValueError("context_policy_rules_invalid")
    return ContextAccessPolicy(
        policy_id=(
            str(data.get("policy_id") or "temporary-validation")
            if temporary
            else str(data["policy_id"])
        ),
        version=data.get("version", 1),
        scope=data.get("scope", "project"),
        rules=[ContextAccessRule(**rule) for rule in rules_payload],
        defaults=data.get("defaults", {}),
        precedence=data.get("precedence", []),
    )


@context_policy_bp.route("/policies", methods=["GET"])
@check_auth
@admin_required
def list_policies():
    project_id = request.args.get("project_id")
    policies = policy_service.list_policies(project_id=project_id)
    return jsonify(
        {
            "status": "success",
            "data": [policy.dict() for policy in policies],
        }
    )


@context_policy_bp.route("/policies", methods=["POST"])
@check_auth
@admin_required
def create_policy():
    try:
        data = _payload()
        policy = _policy_from_payload(data, temporary=False)
        errors = policy_service.validate_policy(policy)
        if errors:
            return (
                jsonify(
                    {
                        "status": "error",
                        "reason_code": "context_policy_validation_failed",
                        "message": "context_policy_validation_failed",
                        "errors": errors,
                    }
                ),
                400,
            )
        policy_db = policy_service.create_policy_record(payload=data, now_ts=time.time())
        return jsonify({"status": "success", "data": policy_db.dict()})
    except (KeyError, TypeError, ValueError):
        return _error("context_policy_payload_invalid", 400)


@context_policy_bp.route("/policies/<policy_id>/latest", methods=["GET"])
@check_auth
@admin_required
def get_latest_policy(policy_id):
    policy_db = policy_service.get_latest_policy(policy_id)
    if not policy_db:
        return _error("context_policy_not_found", 404)
    return jsonify({"status": "success", "data": policy_db.dict()})


@context_policy_bp.route("/validate", methods=["POST"])
@check_auth
@admin_required
def validate_policy_payload():
    try:
        policy = _policy_from_payload(_payload(), temporary=True)
        errors = policy_service.validate_policy(policy)
        return jsonify(
            {
                "status": "success" if not errors else "error",
                "valid": not errors,
                "errors": errors,
            }
        )
    except (KeyError, TypeError, ValueError):
        return _error("context_policy_payload_invalid", 400)
