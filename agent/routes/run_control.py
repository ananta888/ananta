"""RC-020: Run-Control API routes.

Endpoints:
  POST /api/runs/<run_id>/commands        — generic RunCommand (run_id = task_id alias)
  POST /tasks/<task_id>/commands          — task-scoped RunCommand (migration-friendly)
  GET  /api/runs/<run_id>/control-state   — read model for a run/task
  GET  /tasks/<task_id>/control-state     — task-scoped alias
  GET  /api/runs/active-control-state     — dashboard snapshot (all active tasks)

Auth:
  All endpoints require @check_user_auth.
  Mutating commands (pause/cancel/inject/approve/deny) require normal user auth.
  No Worker-direct access; all mutations go through Hub services.

Design:
  Existing /tasks/<id>/pause|resume|cancel|retry routes remain untouched (backward compat).
  New /commands route is the unified Command-Contract that Angular and TUI prefer.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.services.run_control_service import (
    COMMAND_TYPES,
    RunCommandIdempotencyConflictError,
    RunControlAuthorizationError,
    RunControlPrincipal,
    get_run_control_service,
)

run_control_bp = Blueprint("run_control", __name__)


def _principal() -> RunControlPrincipal | None:
    identity = get_request_auth_context()
    if not identity:
        return None
    if isinstance(identity, dict):
        subject = identity.get("sub") or identity.get("username") or ""
        tenant_id = (
            identity.get("tenant_id")
            or identity.get("tenant")
            or identity.get("organization_id")
            or subject
        )
    else:
        subject = getattr(identity, "username", "") or getattr(identity, "id", "")
        tenant_id = (
            getattr(identity, "tenant_id", "")
            or getattr(identity, "organization_id", "")
            or subject
        )
    try:
        return RunControlPrincipal.from_values(tenant_id, subject)
    except ValueError:
        return None


def _resource_not_found():
    return jsonify({"status": "error", "reason_code": "run_control_resource_not_found"}), 404


def _send_command(task_id: str | None = None, goal_id: str | None = None, run_id: str | None = None):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    body = request.get_json(silent=True) or {}
    command_type = str(body.get("type") or "").strip()
    if not command_type:
        return jsonify({"status": "error", "message": "command_type_required"}), 400
    if command_type not in COMMAND_TYPES:
        return jsonify({
            "status": "error",
            "message": "unknown_command_type",
            "allowed": sorted(COMMAND_TYPES),
        }), 400

    payload = dict(body.get("payload") or {})
    idempotency_key = str(body.get("idempotency_key") or "").strip() or None
    raw_command_id = str(body.get("command_id") or "").strip() or None

    svc = get_run_control_service()
    try:
        cmd = svc.send_command(
            command_type=command_type,
            task_id=task_id,
            goal_id=goal_id,
            run_id=run_id or task_id,
            payload=payload,
            requested_by=principal.subject_id,
            idempotency_key=idempotency_key or raw_command_id,
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    except RunCommandIdempotencyConflictError:
        return jsonify(
            {
                "status": "error",
                "reason_code": "runtime_command_idempotency_conflict",
            }
        ), 409
    http_status = 200
    if cmd.status == "rejected_by_policy":
        try:
            policy_status = int(
                cmd.result.get("http_status") or 422
            )
        except (TypeError, ValueError):
            policy_status = 422
        http_status = (
            policy_status
            if 400 <= policy_status < 500
            else 422
        )
    elif cmd.status == "failed":
        http_status = 500
    payload = {"status": "ok", "command": cmd.as_dict()}
    if cmd.status == "rejected_by_policy":
        payload["reason_code"] = str(
            cmd.result.get("reason_code")
            or cmd.result.get("error")
            or "run_command_rejected_by_policy"
        )
    return jsonify(payload), http_status


# ── Run-scoped routes (run_id is task_id in practice) ─────────────────────────

@run_control_bp.post("/api/runs/<run_id>/commands")
@check_user_auth
def run_commands(run_id: str):
    """POST run command; run_id treated as task_id."""
    return _send_command(task_id=str(run_id).strip(), run_id=str(run_id).strip())


@run_control_bp.get("/api/runs/<run_id>/control-state")
@check_user_auth
def run_control_state(run_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    try:
        state = get_run_control_service().get_control_state(
            task_id=str(run_id).strip(),
            run_id=str(run_id).strip(),
            principal=principal,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    return jsonify({"status": "ok", "control_state": state})


@run_control_bp.get("/api/runs/active-control-state")
@check_user_auth
def all_active_control_states():
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    limit = min(int(request.args.get("limit", 50)), 200)
    states = get_run_control_service().get_all_active_control_states(limit=limit, principal=principal)
    return jsonify({"status": "ok", "control_states": states, "count": len(states)})


# ── Task-scoped routes (easy migration from existing task-detail UI) ──────────

@run_control_bp.post("/api/tasks/<task_id>/commands")
@check_user_auth
def task_commands(task_id: str):
    """POST run command scoped to task. Preferred URL for Angular task-detail."""
    return _send_command(task_id=str(task_id).strip())


@run_control_bp.get("/api/tasks/<task_id>/control-state")
@check_user_auth
def task_control_state(task_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    goal_id = str(request.args.get("goal_id") or "").strip() or None
    try:
        state = get_run_control_service().get_control_state(
            task_id=str(task_id).strip(),
            goal_id=goal_id,
            principal=principal,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    return jsonify({"status": "ok", "control_state": state})


@run_control_bp.get("/api/tasks/<task_id>/commands")
@check_user_auth
def list_task_commands(task_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    limit = min(int(request.args.get("limit", 50)), 500)
    try:
        cmds = get_run_control_service().list_commands(
            task_id=str(task_id).strip(),
            limit=limit,
            principal=principal,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    return jsonify({"status": "ok", "commands": cmds, "count": len(cmds)})


# ── Goal-scoped routes ─────────────────────────────────────────────────────────

@run_control_bp.post("/api/goals/<goal_id>/commands")
@check_user_auth
def goal_commands(goal_id: str):
    return _send_command(goal_id=str(goal_id).strip())


@run_control_bp.get("/api/goals/<goal_id>/control-state")
@check_user_auth
def goal_control_state(goal_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    try:
        state = get_run_control_service().get_control_state(
            goal_id=str(goal_id).strip(),
            principal=principal,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    return jsonify({"status": "ok", "control_state": state})


# ── Branch management ─────────────────────────────────────────────────────────

@run_control_bp.get("/api/tasks/<task_id>/branches")
@check_user_auth
def list_task_branches(task_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    service = get_run_control_service()
    if not service.authorize_resources(principal=principal, task_id=str(task_id).strip()):
        return _resource_not_found()
    branches = service.list_branches(task_id=str(task_id).strip(), principal=principal)
    return jsonify({"status": "ok", "branches": [b.as_dict() for b in branches]})


@run_control_bp.post("/api/tasks/<task_id>/branches")
@check_user_auth
def create_task_branch(task_id: str):
    principal = _principal()
    if principal is None:
        return jsonify({"status": "error", "reason_code": "canonical_identity_required"}), 401
    body = request.get_json(silent=True) or {}
    label = str(body.get("label") or "").strip()
    if not label:
        return jsonify({"status": "error", "message": "label_required"}), 400
    try:
        branch = get_run_control_service().create_branch(
            task_id=str(task_id).strip(),
            branch_type=str(body.get("branch_type") or "llm_comparison_variant"),
            label=label,
            description=str(body.get("description") or ""),
            metadata=dict(body.get("metadata") or {}),
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
        )
    except RunControlAuthorizationError:
        return _resource_not_found()
    return jsonify({"status": "ok", "branch": branch.as_dict()}), 201
