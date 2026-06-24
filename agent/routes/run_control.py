"""RC-020: Run-Control API routes.

Endpoints:
  POST /api/runs/<run_id>/commands        — generic RunCommand (run_id = task_id alias)
  POST /tasks/<task_id>/commands          — task-scoped RunCommand (migration-friendly)
  GET  /api/runs/<run_id>/control-state   — read model for a run/task
  GET  /tasks/<task_id>/control-state     — task-scoped alias
  GET  /api/runs/active-control-state     — dashboard snapshot (all active tasks)

Auth:
  All endpoints require @check_auth.
  Mutating commands (pause/cancel/inject/approve/deny) require normal user auth.
  No Worker-direct access; all mutations go through Hub services.

Design:
  Existing /tasks/<id>/pause|resume|cancel|retry routes remain untouched (backward compat).
  New /commands route is the unified Command-Contract that Angular and TUI prefer.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from agent.auth import check_auth
from agent.services.run_control_service import COMMAND_TYPES, get_run_control_service

run_control_bp = Blueprint("run_control", __name__)


def _actor() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "operator")


def _send_command(task_id: str | None = None, goal_id: str | None = None, run_id: str | None = None):
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
    cmd = svc.send_command(
        command_type=command_type,
        task_id=task_id,
        goal_id=goal_id,
        run_id=run_id or task_id,
        payload=payload,
        requested_by=_actor(),
        idempotency_key=idempotency_key or raw_command_id,
    )
    http_status = 200
    if cmd.status == "rejected_by_policy":
        http_status = 422
    elif cmd.status == "failed":
        http_status = 500
    return jsonify({"status": "ok", "command": cmd.as_dict()}), http_status


# ── Run-scoped routes (run_id is task_id in practice) ─────────────────────────

@run_control_bp.post("/api/runs/<run_id>/commands")
@check_auth
def run_commands(run_id: str):
    """POST run command; run_id treated as task_id."""
    return _send_command(task_id=str(run_id).strip(), run_id=str(run_id).strip())


@run_control_bp.get("/api/runs/<run_id>/control-state")
@check_auth
def run_control_state(run_id: str):
    state = get_run_control_service().get_control_state(task_id=str(run_id).strip())
    return jsonify({"status": "ok", "control_state": state})


@run_control_bp.get("/api/runs/active-control-state")
@check_auth
def all_active_control_states():
    limit = min(int(request.args.get("limit", 50)), 200)
    states = get_run_control_service().get_all_active_control_states(limit=limit)
    return jsonify({"status": "ok", "control_states": states, "count": len(states)})


# ── Task-scoped routes (easy migration from existing task-detail UI) ──────────

@run_control_bp.post("/api/tasks/<task_id>/commands")
@check_auth
def task_commands(task_id: str):
    """POST run command scoped to task. Preferred URL for Angular task-detail."""
    return _send_command(task_id=str(task_id).strip())


@run_control_bp.get("/api/tasks/<task_id>/control-state")
@check_auth
def task_control_state(task_id: str):
    goal_id = str(request.args.get("goal_id") or "").strip() or None
    state = get_run_control_service().get_control_state(
        task_id=str(task_id).strip(),
        goal_id=goal_id,
    )
    return jsonify({"status": "ok", "control_state": state})


@run_control_bp.get("/api/tasks/<task_id>/commands")
@check_auth
def list_task_commands(task_id: str):
    limit = min(int(request.args.get("limit", 50)), 500)
    cmds = get_run_control_service().list_commands(task_id=str(task_id).strip(), limit=limit)
    return jsonify({"status": "ok", "commands": cmds, "count": len(cmds)})


# ── Goal-scoped routes ─────────────────────────────────────────────────────────

@run_control_bp.post("/api/goals/<goal_id>/commands")
@check_auth
def goal_commands(goal_id: str):
    return _send_command(goal_id=str(goal_id).strip())


@run_control_bp.get("/api/goals/<goal_id>/control-state")
@check_auth
def goal_control_state(goal_id: str):
    state = get_run_control_service().get_control_state(goal_id=str(goal_id).strip())
    return jsonify({"status": "ok", "control_state": state})


# ── Branch management ─────────────────────────────────────────────────────────

@run_control_bp.get("/api/tasks/<task_id>/branches")
@check_auth
def list_task_branches(task_id: str):
    branches = get_run_control_service().list_branches(task_id=str(task_id).strip())
    return jsonify({"status": "ok", "branches": [b.as_dict() for b in branches]})


@run_control_bp.post("/api/tasks/<task_id>/branches")
@check_auth
def create_task_branch(task_id: str):
    body = request.get_json(silent=True) or {}
    label = str(body.get("label") or "").strip()
    if not label:
        return jsonify({"status": "error", "message": "label_required"}), 400
    branch = get_run_control_service().create_branch(
        task_id=str(task_id).strip(),
        branch_type=str(body.get("branch_type") or "llm_comparison_variant"),
        label=label,
        description=str(body.get("description") or ""),
        metadata=dict(body.get("metadata") or {}),
    )
    return jsonify({"status": "ok", "branch": branch.as_dict()}), 201
