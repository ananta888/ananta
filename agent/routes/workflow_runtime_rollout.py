"""Admin-only Hub route for evidence-gated workflow-runtime promotion."""

from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime_rollout_runtime import (
    WorkflowRuntimeRolloutConfigurationError,
    get_workflow_runtime_promotion_service,
)
from agent.services.workflow_runtime_rollout_service import (
    WorkflowRolloutPolicy,
    rollout_scope_from_plan,
)

workflow_runtime_rollout_bp = Blueprint(
    "workflow_runtime_rollout",
    __name__,
    url_prefix="/api/workflow-runtime/rollout",
)

_MAX_BODY_BYTES = 256 * 1024
_FIELDS = frozenset(
    {
        "policy",
        "plan",
        "expected_revision",
        "reason_code",
        "change_id",
        "approval_id",
    }
)


@workflow_runtime_rollout_bp.after_request
def _disable_promotion_response_caching(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@workflow_runtime_rollout_bp.post("/promotions")
@check_strict_auth
def promote_workflow_runtime():
    identity = dict(get_request_auth_context() or {})
    if not _is_admin(identity):
        return jsonify({"status": "error", "reason_code": "workflow_rollout_admin_required"}), 403
    try:
        body = _body()
        if set(body) - _FIELDS:
            raise ValueError("workflow_rollout_promotion_fields_forbidden")
        policy_raw = body.get("policy")
        plan_raw = body.get("plan")
        if not isinstance(policy_raw, Mapping) or not isinstance(plan_raw, Mapping):
            raise ValueError("workflow_rollout_promotion_contract_required")
        policy = WorkflowRolloutPolicy.from_mapping(policy_raw)
        plan = ExecutionPlan.from_mapping(dict(plan_raw))
        identity_tenant = str(identity.get("tenant_id") or "").strip()
        if not identity_tenant or plan.tenant_id != identity_tenant:
            raise ValueError("workflow_rollout_promotion_tenant_mismatch")
        plan_scope = rollout_scope_from_plan(plan)
        if policy.scope not in plan_scope.lineage():
            raise ValueError("workflow_rollout_promotion_scope_mismatch")
        if policy.scope.scope_type == "project" and not _is_global_admin(identity):
            raise PermissionError("workflow_rollout_project_scope_global_admin_required")
        if policy.scope.scope_type != "project" and policy.scope.tenant_id != plan.tenant_id:
            raise ValueError("workflow_rollout_promotion_tenant_mismatch")
        if policy.scope.workflow_id and policy.scope.workflow_id != plan.workflow_id:
            raise ValueError("workflow_rollout_promotion_workflow_mismatch")
        expected_revision = int(body.get("expected_revision"))
        if expected_revision < 0:
            raise ValueError("workflow_rollout_revision_invalid")
        actor = str(identity.get("sub") or identity.get("username") or "").strip()
        approval_id = str(body.get("approval_id") or "").strip()
        result = get_workflow_runtime_promotion_service().promote(
            policy=policy,
            plan=plan,
            expected_revision=expected_revision,
            actor_id=actor,
            reason_code=str(body.get("reason_code") or "").strip(),
            change_id=str(body.get("change_id") or "").strip(),
            approval_id=approval_id,
        )
    except WorkflowRuntimeRolloutConfigurationError as exc:
        return jsonify({"status": "error", "reason_code": _safe_reason_code(exc)}), 503
    except RequestEntityTooLarge:
        return jsonify({"status": "error", "reason_code": "workflow_rollout_payload_too_large"}), 413
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "reason_code": _safe_reason_code(exc)}), 400
    except PermissionError as exc:
        return jsonify({"status": "error", "reason_code": _safe_reason_code(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"status": "error", "reason_code": _safe_reason_code(exc)}), 409
    log_audit(
        "workflow_runtime_performance_promotion",
        {
            "actor": actor,
            "tenant_id": plan.tenant_id,
            "scope_key": policy.scope.scope_key,
            "approval_id": approval_id,
            "change_id": str(body.get("change_id") or ""),
            "performance_evidence_ref": result.performance_evidence.evidence_ref,
            "shadow_comparison_ref": (result.shadow_comparison_evidence.evidence_ref),
            "source_revision": result.performance_evidence.source_revision,
            "revision": result.stored_policy.revision,
        },
    )
    return jsonify(
        {
            "status": "ok",
            "promotion": {
                "policy": result.stored_policy.policy.to_dict(),
                "revision": result.stored_policy.revision,
                "runtime_id": result.runtime_selection.runtime_id,
                "selection_audit_ref": result.runtime_selection.audit_ref,
                "performance_evidence_ref": (result.performance_evidence.evidence_ref),
                "shadow_comparison_ref": (result.shadow_comparison_evidence.evidence_ref),
                "source_revision": result.performance_evidence.source_revision,
                "approval_id": approval_id,
            },
        }
    ), 201


def _body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = _MAX_BODY_BYTES
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("workflow_rollout_json_required")
    return value


def _is_admin(identity: Mapping[str, Any]) -> bool:
    roles = identity.get("roles")
    values = (
        {str(value).strip().lower() for value in roles} if isinstance(roles, (list, tuple, set, frozenset)) else set()
    )
    role = str(identity.get("role") or "").strip().lower()
    privileged = {"admin", "superadmin", "system_admin"}
    return role in privileged or bool(values & privileged)


def _is_global_admin(identity: Mapping[str, Any]) -> bool:
    roles = identity.get("roles")
    values = (
        {str(value).strip().lower() for value in roles}
        if isinstance(roles, (list, tuple, set, frozenset))
        else set()
    )
    role = str(identity.get("role") or "").strip().lower()
    return role in {"superadmin", "system_admin"} or bool(
        values & {"superadmin", "system_admin"}
    )


def _safe_reason_code(error: Exception) -> str:
    value = str(error).split(":", 1)[0].strip()
    if value.startswith("workflow_rollout_") and value.replace("_", "").isalnum():
        return value
    return "workflow_rollout_request_failed"


__all__ = ["workflow_runtime_rollout_bp"]
