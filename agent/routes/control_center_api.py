"""Control-Center API surface (B01-B12).

Additive adapter layer: exposes stable /api contracts for the Angular control center
without breaking existing route behavior.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import TaskDB
from agent.routes.tasks.status import normalize_task_status
from agent.services.repository_registry import get_repository_registry
from agent.services.share_session_service import get_share_session_service

control_center_api_bp = Blueprint("control_center_api", __name__, url_prefix="/api")

# B08 mapping for task->session relation until a dedicated persisted session model is introduced.
_TASK_SESSION_LINKS: dict[str, str] = {}  # session_id -> task_id
_APPROVAL_EVENTS: list[dict[str, Any]] = []


def _repos():
    return get_repository_registry()


def _user_id() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "").strip()


def _project_item(team: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(team, "id", "") or ""),
        "name": str(getattr(team, "name", "") or ""),
        "description": str(getattr(team, "description", "") or ""),
        "is_active": bool(getattr(team, "is_active", False)),
        "root": None,
    }


def _task_item(task: Any) -> dict[str, Any]:
    verification_status = dict(getattr(task, "verification_status", None) or {})
    return {
        "id": str(getattr(task, "id", "") or ""),
        "title": str(getattr(task, "title", "") or ""),
        "description": str(getattr(task, "description", "") or ""),
        "status": normalize_task_status(str(getattr(task, "status", "todo") or "todo")),
        "priority": str(getattr(task, "priority", "Medium") or "Medium"),
        "project_id": str(getattr(task, "team_id", "") or ""),
        "team_id": str(getattr(task, "team_id", "") or ""),
        "assigned_agent_url": str(getattr(task, "assigned_agent_url", "") or ""),
        "goal_id": str(getattr(task, "goal_id", "") or "") or None,
        "verification_status": verification_status,
        "created_at": float(getattr(task, "created_at", 0.0) or 0.0),
        "updated_at": float(getattr(task, "updated_at", 0.0) or 0.0),
    }


def _session_item(session: dict[str, Any]) -> dict[str, Any]:
    sid = str(session.get("id") or "")
    return {
        "id": sid,
        "task_id": _TASK_SESSION_LINKS.get(sid),
        "title": str(session.get("title") or "Shared Session"),
        "mode": str(session.get("mode") or "relay"),
        "transport": str(session.get("transport") or "hub_relay"),
        "owner_user_id": str(session.get("owner_user_id") or ""),
        "permissions": dict(session.get("permissions") or {}),
        "status": "running" if session.get("revoked_at") is None else "cancelled",
        "created_at": float(session.get("created_at") or 0.0),
        "expires_at": session.get("expires_at"),
        "revoked_at": session.get("revoked_at"),
    }


@control_center_api_bp.route("/projects", methods=["GET"])
@check_auth
def list_projects():
    """B01: GET /api/projects"""
    teams = _repos().team_repo.get_all() or []
    items = [_project_item(team) for team in teams]
    return api_response(data={"items": items, "count": len(items)})


@control_center_api_bp.route("/projects/<project_id>/tasks", methods=["GET"])
@check_auth
def list_project_tasks(project_id: str):
    """B02: GET /api/projects/{projectId}/tasks"""
    tasks = _repos().task_repo.get_all() or []
    items = [_task_item(task) for task in tasks if str(getattr(task, "team_id", "") or "") == project_id]
    return api_response(data={"items": items, "count": len(items), "project_id": project_id})


@control_center_api_bp.route("/tasks/<task_id>", methods=["GET"])
@check_auth
def get_task_detail(task_id: str):
    """B03: GET /api/tasks/{taskId} unified detail"""
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    t = _task_item(task)
    sid = next((session_id for session_id, linked_task_id in _TASK_SESSION_LINKS.items() if linked_task_id == task_id), None)
    session_payload = None
    if sid:
        raw = get_share_session_service().get_session(sid)
        if isinstance(raw, dict):
            session_payload = _session_item(raw)

    policy_decisions = [
        {
            "id": str(getattr(p, "id", "") or ""),
            "decision_type": str(getattr(p, "decision_type", "") or ""),
            "status": str(getattr(p, "status", "") or ""),
            "reasons": list(getattr(p, "reasons", None) or []),
            "details": dict(getattr(p, "details", None) or {}),
            "created_at": float(getattr(p, "created_at", 0.0) or 0.0),
        }
        for p in (_repos().policy_decision_repo.get_all() or [])
        if str(getattr(p, "task_id", "") or "") == task_id
    ]

    return api_response(
        data={
            "task": t,
            "session": session_payload,
            "policy_decisions": policy_decisions,
            "artifacts": [],
            "verification": dict(getattr(task, "verification_status", None) or {}),
        }
    )


@control_center_api_bp.route("/tasks", methods=["POST"])
@check_auth
def create_task():
    """B04: POST /api/tasks"""
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip()
    if not title:
        return api_response(status="error", message="title_required", code=400)

    task = TaskDB(
        id=str(uuid.uuid4()),
        title=title,
        description=str(body.get("description") or ""),
        status=normalize_task_status(str(body.get("status") or "backlog")),
        priority=str(body.get("priority") or "Medium"),
        team_id=(str(body.get("project_id") or "").strip() or None),
        task_kind=(str(body.get("task_kind") or "").strip() or None),
    )
    saved = _repos().task_repo.save(task)
    log_audit("control_center_task_created", {"task_id": saved.id, "actor": _user_id()})
    return api_response(data={"task": _task_item(saved)}, code=201)


@control_center_api_bp.route("/tasks/<task_id>", methods=["PATCH"])
@check_auth
def patch_task(task_id: str):
    """B05: PATCH /api/tasks/{taskId}"""
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    body = request.get_json(silent=True) or {}
    if "title" in body:
        task.title = str(body.get("title") or "").strip() or task.title
    if "description" in body:
        task.description = str(body.get("description") or "")
    if "priority" in body:
        task.priority = str(body.get("priority") or task.priority)
    if "status" in body:
        task.status = normalize_task_status(str(body.get("status") or task.status))
    task.updated_at = time.time()

    saved = _repos().task_repo.save(task)
    log_audit("control_center_task_updated", {"task_id": saved.id, "actor": _user_id(), "status": saved.status})
    return api_response(data={"task": _task_item(saved)})


@control_center_api_bp.route("/sessions", methods=["GET"])
@check_auth
def list_sessions():
    """B06: GET /api/sessions"""
    user_id = _user_id()
    if not user_id:
        return api_response(status="error", message="not_authenticated", code=401)

    service = get_share_session_service()
    owned = service.list_sessions_for_owner(user_id)
    joined = service.list_sessions_as_participant(user_id)
    merged: dict[str, dict[str, Any]] = {}
    for item in [*owned, *joined]:
        sid = str(item.get("id") or "")
        if not sid:
            continue
        merged[sid] = _session_item(item)

    task_id = str(request.args.get("task_id") or "").strip()
    items = list(merged.values())
    if task_id:
        items = [it for it in items if str(it.get("task_id") or "") == task_id]

    return api_response(data={"items": items, "count": len(items)})


@control_center_api_bp.route("/sessions/<session_id>", methods=["GET"])
@check_auth
def get_session(session_id: str):
    """B07: GET /api/sessions/{sessionId}"""
    raw = get_share_session_service().get_session(session_id)
    if not isinstance(raw, dict):
        return api_response(status="error", message="not_found", code=404)

    participants = get_share_session_service().get_participants(session_id)
    decisions = [
        {
            "id": str(getattr(p, "id", "") or ""),
            "decision_type": str(getattr(p, "decision_type", "") or ""),
            "status": str(getattr(p, "status", "") or ""),
            "reasons": list(getattr(p, "reasons", None) or []),
            "details": dict(getattr(p, "details", None) or {}),
            "created_at": float(getattr(p, "created_at", 0.0) or 0.0),
        }
        for p in (_repos().policy_decision_repo.get_all() or [])
        if str((getattr(p, "details", None) or {}).get("session_id") or "") == session_id
    ]

    return api_response(data={"session": _session_item(raw), "participants": participants, "policy_decisions": decisions})


@control_center_api_bp.route("/tasks/<task_id>/sessions", methods=["POST"])
@check_auth
def create_task_session(task_id: str):
    """B08: POST /api/tasks/{taskId}/sessions"""
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="task_not_found", code=404)

    user_id = _user_id()
    if not user_id:
        return api_response(status="error", message="not_authenticated", code=401)

    body = request.get_json(silent=True) or {}
    device_id = str(body.get("owner_device_id") or request.headers.get("X-Ananta-Device-Id") or "web-control-center").strip()
    permissions = body.get("permissions") if isinstance(body.get("permissions"), dict) else {"chat": True, "view_tui": True}
    session = get_share_session_service().create_session(
        owner_user_id=user_id,
        owner_device_id=device_id,
        title=str(body.get("title") or task.title or "Task Session").strip() or "Task Session",
        mode=str(body.get("mode") or "relay"),
        transport=str(body.get("transport") or "hub_relay"),
        permissions=permissions,
        expires_at=body.get("expires_at") if isinstance(body.get("expires_at"), (int, float)) else None,
    )
    sid = str(session.get("id") or "")
    if sid:
        _TASK_SESSION_LINKS[sid] = task_id

    log_audit("control_center_task_session_created", {"task_id": task_id, "session_id": sid, "actor": user_id})
    return api_response(data={"session": _session_item(session)}, code=201)


@control_center_api_bp.route("/sessions/<session_id>/cancel", methods=["POST"])
@check_auth
def cancel_session(session_id: str):
    """B09: POST /api/sessions/{sessionId}/cancel"""
    user_id = _user_id()
    ok, reason = get_share_session_service().revoke_session(session_id=session_id, actor_user_id=user_id)
    if not ok:
        if reason == "forbidden":
            return api_response(status="error", message=reason, code=403)
        if reason == "session_not_found":
            return api_response(status="error", message=reason, code=404)
        return api_response(status="error", message=reason or "cancel_failed", code=400)
    log_audit("control_center_session_cancelled", {"session_id": session_id, "actor": user_id})
    return api_response(data={"session_id": session_id, "status": "cancelled"})


@control_center_api_bp.route("/sessions/<session_id>/policy-decisions", methods=["GET"])
@check_auth
def list_session_policy_decisions(session_id: str):
    """B10: GET /api/sessions/{sessionId}/policy-decisions"""
    rows = _repos().policy_decision_repo.get_all() or []
    items = []
    for row in rows:
        details = dict(getattr(row, "details", None) or {})
        if str(details.get("session_id") or "") != session_id:
            continue
        items.append(
            {
                "id": str(getattr(row, "id", "") or ""),
                "decision": str(getattr(row, "status", "") or ""),
                "decision_type": str(getattr(row, "decision_type", "") or ""),
                "reason": "; ".join(list(getattr(row, "reasons", None) or [])),
                "matched_rule_ids": list(details.get("matched_rule_ids") or []),
                "created_at": float(getattr(row, "created_at", 0.0) or 0.0),
            }
        )

    # Include explicit narrow approvals captured via B11
    items.extend([
        {
            "id": str(entry.get("id") or ""),
            "decision": "allow",
            "decision_type": "manual_approval",
            "reason": "narrow_approval",
            "matched_rule_ids": [],
            "created_at": float(entry.get("created_at") or 0.0),
            "action_id": entry.get("action_id"),
            "tool_call_id": entry.get("tool_call_id"),
        }
        for entry in _APPROVAL_EVENTS
        if str(entry.get("session_id") or "") == session_id
    ])

    items.sort(key=lambda x: float(x.get("created_at") or 0.0), reverse=True)
    return api_response(data={"items": items, "count": len(items)})


@control_center_api_bp.route("/policy/approve", methods=["POST"])
@check_auth
def approve_policy_action():
    """B11: POST /api/policy/approve narrow approval"""
    body = request.get_json(silent=True) or {}
    action_id = str(body.get("action_id") or "").strip()
    tool_call_id = str(body.get("tool_call_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()

    if not action_id or not tool_call_id:
        return api_response(status="error", message="action_id_and_tool_call_id_required", code=400)
    if str(body.get("scope") or "single_action") != "single_action":
        return api_response(status="error", message="wildcard_approval_forbidden", code=403)

    event = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "action_id": action_id,
        "tool_call_id": tool_call_id,
        "actor": _user_id(),
        "created_at": time.time(),
    }
    _APPROVAL_EVENTS.append(event)
    log_audit("control_center_policy_approved", event)
    return api_response(data={"approved": True, "scope": "single_action", **event})


@control_center_api_bp.route("/workers", methods=["GET"])
@check_auth
def list_workers():
    """B12: GET /api/workers unified worker registry"""
    agents = _repos().agent_repo.get_all() or []
    items = []
    for agent in agents:
        capabilities = list(getattr(agent, "capabilities", None) or [])
        runtime_targets = list(getattr(agent, "runtime_targets", None) or [])
        runtime = "local"
        if runtime_targets:
            first = runtime_targets[0]
            runtime = str((first or {}).get("runtime_kind") or (first or {}).get("kind") or runtime)
        status = str(getattr(agent, "status", "offline") or "offline")
        items.append(
            {
                "id": str(getattr(agent, "name", "") or getattr(agent, "url", "") or ""),
                "url": str(getattr(agent, "url", "") or ""),
                "role": str(getattr(agent, "role", "worker") or "worker"),
                "health": status,
                "runtime": runtime,
                "capabilities": capabilities,
                "worker_roles": list(getattr(agent, "worker_roles", None) or []),
                "boundary": "local-only" if "cloud" not in runtime.lower() else "cloud-allowed",
                "last_seen": float(getattr(agent, "last_seen", 0.0) or 0.0),
            }
        )
    return api_response(data={"items": items, "count": len(items)})
