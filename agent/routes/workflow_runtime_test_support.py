"""Private Compose-E2E support for the fail-closed workflow rollout boundary.

The endpoint in this module is registered only by the explicit Compose-E2E
runtime context and remains fail-closed if manually mounted elsewhere. It
accepts no caller-defined policy: the only supported operation is
provisioning one isolated Native project scope for a real Compose smoke run.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, Flask, abort, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.config import settings
from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)
from agent.services.workflow_runtime_compose_e2e_support import (
    COMPOSE_E2E_PROJECT_ID,
    compose_e2e_test_support_enabled,
)
from agent.services.workflow_runtime_rollout_persistence import (
    SQLAlchemyWorkflowRolloutPolicyStore,
    WorkflowRolloutPersistenceError,
)
from agent.services.workflow_runtime_rollout_service import (
    WorkflowRolloutAuditEvent,
    WorkflowRolloutPolicy,
    WorkflowRolloutScope,
)

workflow_runtime_test_support_bp = Blueprint(
    "workflow_runtime_test_support",
    __name__,
    url_prefix="/test/workflow-runtime",
)

_ALLOWED_FIELDS = frozenset({"project_id"})
_NATIVE_CAPABILITIES = (
    "audit",
    "authorization",
    "policy",
    "side_effect_guard",
)


def _rollout_store() -> SQLAlchemyWorkflowRolloutPolicyStore:
    from agent.database import engine

    return SQLAlchemyWorkflowRolloutPolicyStore(engine)


def register_workflow_runtime_test_support(app: Flask) -> bool:
    """Mount the private blueprint only in the explicit Compose-E2E Hub."""

    if not compose_e2e_test_support_enabled():
        return False
    app.register_blueprint(workflow_runtime_test_support_bp)
    return True


@workflow_runtime_test_support_bp.before_request
def _fail_closed_outside_compose_e2e() -> None:
    if not compose_e2e_test_support_enabled():
        abort(404)


@workflow_runtime_test_support_bp.get("/native-health")
def native_test_runtime_health():
    """Expose a bounded health observation for the in-process Native bridge.

    Production selection intentionally requires observed runtime health.  The
    isolated Compose-E2E Hub owns its Native bridge itself, so it cannot rely
    on a separately registered worker heartbeat.  This test-context-only
    endpoint gives the regular health adapter a real HTTP observation without
    weakening the production fail-closed default.
    """

    return jsonify(
        {
            "schema": "ananta.workflow_runtime_test_health.v1",
            "runtime_id": "ananta-native",
            "runtime_version": "1.0.0",
            "status": "ready",
            "ready": True,
        }
    )


def _admin_identity() -> tuple[str, str] | None:
    identity = dict(get_request_auth_context() or {})
    role = str(identity.get("role") or "").lower()
    roles = identity.get("roles")
    role_values = (
        {str(value).lower() for value in roles}
        if isinstance(roles, (list, tuple, set, frozenset))
        else set()
    )
    if role != "admin" and "admin" not in role_values:
        return None
    try:
        subject = require_canonical_identity(
            identity.get("sub") or identity.get("username"),
            field_name="subject_id",
        )
        tenant_id = require_canonical_identity(
            identity.get("tenant_id"),
            field_name="tenant_id",
        )
    except IdentityValidationError:
        return None
    try:
        expected = require_canonical_identity(
            settings.initial_admin_user,
            field_name="initial_admin_user",
        )
    except IdentityValidationError:
        return None
    return (tenant_id, subject) if tenant_id == expected and subject == expected else None


@workflow_runtime_test_support_bp.post("/native-rollout")
@check_user_auth
def provision_native_test_rollout():
    principal = _admin_identity()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "admin_required"}), 403

    body: Any = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != _ALLOWED_FIELDS:
        return jsonify({"status": "error", "reason_code": "test_rollout_contract_invalid"}), 400
    try:
        project_id = require_canonical_identity(
            body.get("project_id"),
            field_name="project_id",
        )
        if project_id != COMPOSE_E2E_PROJECT_ID:
            raise ValueError("compose_e2e_project_scope_required")
        scope = WorkflowRolloutScope(project_id=project_id)
        policy = WorkflowRolloutPolicy(
            scope=scope,
            policy_version="compose-e2e-native-v1",
            mode="live",
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native",),
            required_capabilities=_NATIVE_CAPABILITIES,
            allowed_side_effect_classes=("none", "read"),
            fallback_semantics="none",
            evidence_refs=("compose-e2e-test-only",),
        )
        policy.assert_valid()
    except (IdentityValidationError, ValueError):
        return jsonify({"status": "error", "reason_code": "test_rollout_scope_invalid"}), 422

    store = _rollout_store()
    existing = store.get(scope)
    if existing is not None:
        if existing.policy != policy:
            return jsonify({"status": "error", "reason_code": "test_rollout_scope_conflict"}), 409
        return jsonify(_response(existing.policy, existing.revision)), 200

    tenant_id, subject_id = principal
    audit = WorkflowRolloutAuditEvent(
        event_id=f"compose-e2e-{uuid.uuid4().hex}",
        scope=scope,
        action="test_fixture_live_policy",
        actor_id=subject_id,
        reason_code="compose_e2e_native_runtime",
        occurred_at=time.time(),
        details={"test_only": True, "tenant_id": tenant_id},
    )
    try:
        stored = store.commit(
            policy,
            expected_revision=0,
            parent_revision=None,
            audit=audit,
        )
    except WorkflowRolloutPersistenceError:
        existing = store.get(scope)
        if existing is not None and existing.policy == policy:
            return jsonify(_response(existing.policy, existing.revision)), 200
        return jsonify({"status": "error", "reason_code": "test_rollout_persistence_conflict"}), 409
    return jsonify(_response(stored.policy, stored.revision)), 201


def _response(policy: WorkflowRolloutPolicy, revision: int) -> dict[str, Any]:
    return {
        "status": "ok",
        "schema": "ananta.workflow_runtime_test_rollout.v1",
        "project_id": policy.scope.project_id,
        "runtime_id": policy.preferred_runtime,
        "revision": int(revision),
    }


__all__ = [
    "COMPOSE_E2E_PROJECT_ID",
    "compose_e2e_test_support_enabled",
    "register_workflow_runtime_test_support",
    "workflow_runtime_test_support_bp",
]
