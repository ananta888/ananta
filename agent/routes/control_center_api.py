"""Control-Center API surface (B01-B12).

Additive adapter layer: exposes stable /api contracts for the Angular control center
without breaking existing route behavior.
"""
from __future__ import annotations

import time
import uuid
import json
import threading
import hashlib
from typing import Any

from flask import Blueprint, Response, g, request
import jwt

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import AgentSessionDB, PolicySnapshotDB, TaskDB, ToolCallDB
from agent.routes.tasks.status import normalize_task_status
from agent.services.repository_registry import get_repository_registry
from agent.services.share_session_service import get_share_session_service

control_center_api_bp = Blueprint("control_center_api", __name__, url_prefix="/api")

_EVENT_SEQUENCE: int = 0
_EVENT_LOCK = threading.Lock()
_EVENT_COND = threading.Condition(_EVENT_LOCK)
_EVENT_LOG: list[dict[str, Any]] = []
_EVENT_MAX = 2000
_EVENT_POLL_THREAD: threading.Thread | None = None
_EVENT_LAST_TASK_TS = 0.0
_EVENT_LAST_POLICY_TS = 0.0


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


_ALLOWED_AGENT_SESSION_STATUSES = {
    "idle",
    "proposed",
    "running",
    "waiting_for_approval",
    "blocked",
    "review",
    "verified",
    "done",
    "failed",
    "cancelled",
}


def _normalize_agent_session_status(raw: str | None) -> str:
    status = str(raw or "").strip().lower()
    if status in _ALLOWED_AGENT_SESSION_STATUSES:
        return status
    if status in {"canceled"}:
        return "cancelled"
    return "idle"


def _agent_session_item(session: AgentSessionDB) -> dict[str, Any]:
    snapshot = _repos().policy_snapshot_repo.get_by_id(str(session.policy_snapshot_id or "")) if session.policy_snapshot_id else None
    return {
        "id": str(session.id or ""),
        "task_id": str(session.task_id or "") or None,
        "title": str(session.title or "Agent Session"),
        "status": _normalize_agent_session_status(session.status),
        "transport": str(session.transport or "hub_relay"),
        "mode": str(session.mode or "relay"),
        "owner_user_id": str(session.owner_user_id or ""),
        "session_kind": str(session.session_kind or "agent_execution"),
        "worker_id": str(session.worker_id or "") or None,
        "worker_type": str(session.worker_type or "") or None,
        "model": str(session.model or "") or None,
        "runtime": str(session.runtime or "") or None,
        "policy_snapshot_id": str(session.policy_snapshot_id or "") or None,
        "context_scope_id": str(session.context_scope_id or "") or None,
        "created_at": float(session.created_at or 0.0),
        "updated_at": float(session.updated_at or 0.0),
        "cancelled_at": session.cancelled_at,
        "policy_snapshot": _policy_snapshot_item(snapshot) if snapshot else None,
    }


def _tool_call_item(row: ToolCallDB) -> dict[str, Any]:
    return {
        "id": str(row.id or ""),
        "session_id": str(row.session_id or ""),
        "task_id": str(row.task_id or "") or None,
        "action_id": str(row.action_id or ""),
        "tool_name": str(row.tool_name or ""),
        "status": str(row.status or ""),
        "risk_level": str(row.risk_level or "medium"),
        "target_path": str(row.target_path or "") or None,
        "created_at": float(row.created_at or 0.0),
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error_message": row.error_message,
    }


def _policy_snapshot_item(snapshot: PolicySnapshotDB) -> dict[str, Any]:
    return {
        "id": str(snapshot.id or ""),
        "session_id": str(snapshot.session_id or ""),
        "task_id": str(snapshot.task_id or "") or None,
        "policy_version": str(snapshot.policy_version or "v1"),
        "risk_level": str(snapshot.risk_level or "medium"),
        "allowed_tools": list(snapshot.allowed_tools_json or []),
        "denied_tools": list(snapshot.denied_tools_json or []),
        "allowed_paths": list(snapshot.allowed_paths_json or []),
        "denied_paths": list(snapshot.denied_paths_json or []),
        "cloud_allowed": bool(snapshot.cloud_allowed),
        "runtime_boundary": str(snapshot.runtime_boundary or "unknown"),
        "requires_human_approval": bool(snapshot.requires_human_approval),
        "approval_reason": snapshot.approval_reason,
        "created_at": float(snapshot.created_at or 0.0),
    }


def _artifact_item(artifact: Any) -> dict[str, Any]:
    metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
    return {
        "id": str(getattr(artifact, "id", "") or ""),
        "latest_media_type": str(getattr(artifact, "latest_media_type", "") or "") or None,
        "latest_filename": str(getattr(artifact, "latest_filename", "") or "") or None,
        "artifact_metadata": metadata,
        "created_at": float(getattr(artifact, "created_at", 0.0) or 0.0),
        "updated_at": float(getattr(artifact, "updated_at", 0.0) or 0.0),
    }


def _next_event_id() -> str:
    global _EVENT_SEQUENCE
    _EVENT_SEQUENCE += 1
    return f"cc-{int(time.time() * 1000)}-{_EVENT_SEQUENCE}"


def _append_event(channel: str, event_type: str, timestamp: float, payload: dict[str, Any]) -> None:
    event = {
        "id": _next_event_id(),
        "channel": channel,
        "type": event_type,
        "timestamp": float(timestamp),
        "payload": payload,
    }
    with _EVENT_COND:
        _EVENT_LOG.append(event)
        if len(_EVENT_LOG) > _EVENT_MAX:
            del _EVENT_LOG[0:len(_EVENT_LOG) - _EVENT_MAX]
        _EVENT_COND.notify_all()


def _event_poll_loop() -> None:
    global _EVENT_LAST_TASK_TS, _EVENT_LAST_POLICY_TS
    while True:
        try:
            repos = _repos()
            task_rows = repos.task_repo.get_all() or []
            for task in task_rows:
                updated_at = float(getattr(task, "updated_at", 0.0) or 0.0)
                if updated_at <= _EVENT_LAST_TASK_TS:
                    continue
                _append_event("task", "task_updated", updated_at, _task_item(task))
                if updated_at > _EVENT_LAST_TASK_TS:
                    _EVENT_LAST_TASK_TS = updated_at

            policy_rows = repos.policy_decision_repo.get_all() or []
            for decision in policy_rows:
                created_at = float(getattr(decision, "created_at", 0.0) or 0.0)
                if created_at <= _EVENT_LAST_POLICY_TS:
                    continue
                details = dict(getattr(decision, "details", None) or {})
                _append_event(
                    "policy",
                    "policy_decision",
                    created_at,
                    {
                        "decision_id": str(getattr(decision, "id", "") or ""),
                        "status": str(getattr(decision, "status", "") or ""),
                        "decision_type": str(getattr(decision, "decision_type", "") or ""),
                        "task_id": str(getattr(decision, "task_id", "") or ""),
                        "session_id": str(details.get("session_id") or ""),
                    },
                )
                if created_at > _EVENT_LAST_POLICY_TS:
                    _EVENT_LAST_POLICY_TS = created_at
        except Exception:
            # Keep stream infrastructure alive even if one poll cycle fails.
            pass
        time.sleep(2)


def _ensure_event_poller() -> None:
    global _EVENT_POLL_THREAD
    with _EVENT_LOCK:
        if _EVENT_POLL_THREAD and _EVENT_POLL_THREAD.is_alive():
            return
        _EVENT_POLL_THREAD = threading.Thread(target=_event_poll_loop, daemon=True, name="control-center-event-poller")
        _EVENT_POLL_THREAD.start()


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
    linked_sessions = _repos().agent_session_repo.get_by_task_id(task_id)
    primary_session = linked_sessions[0] if linked_sessions else None
    session_payload = _agent_session_item(primary_session) if primary_session else None

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
    artifacts = []
    for artifact in (_repos().artifact_repo.get_all() or []):
        metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
        if str(metadata.get("task_id") or "") != task_id:
            continue
        artifacts.append(_artifact_item(artifact))

    return api_response(
        data={
            "task": t,
            "session": session_payload,
            "policy_decisions": policy_decisions,
            "artifacts": artifacts,
            "sessions": [_agent_session_item(s) for s in linked_sessions],
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

    persisted = _repos().agent_session_repo.get_all() or []
    owned_rows = [row for row in persisted if str(getattr(row, "owner_user_id", "") or "") == user_id]
    task_id = str(request.args.get("task_id") or "").strip()
    items = [_agent_session_item(row) for row in owned_rows]
    if task_id:
        items = [it for it in items if str(it.get("task_id") or "") == task_id]

    return api_response(data={"items": items, "count": len(items)})


@control_center_api_bp.route("/sessions/<session_id>", methods=["GET"])
@check_auth
def get_session(session_id: str):
    """B07: GET /api/sessions/{sessionId}"""
    persisted = _repos().agent_session_repo.get_by_id(session_id)
    if persisted is None:
        return api_response(status="error", message="not_found", code=404)
    participants: list[dict[str, Any]] = []
    if persisted.share_session_id:
        participants = get_share_session_service().get_participants(str(persisted.share_session_id))
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

    tool_calls = [_tool_call_item(item) for item in _repos().tool_call_repo.get_by_session_id(session_id)]
    return api_response(data={"session": _agent_session_item(persisted), "participants": participants, "policy_decisions": decisions, "tool_calls": tool_calls})


@control_center_api_bp.route("/sessions/<session_id>/tool-calls", methods=["GET"])
@check_auth
def list_session_tool_calls(session_id: str):
    persisted = _repos().agent_session_repo.get_by_id(session_id)
    if persisted is None:
        return api_response(status="error", message="not_found", code=404)
    if str(persisted.owner_user_id or "") != _user_id():
        return api_response(status="error", message="forbidden", code=403)
    items = [_tool_call_item(item) for item in _repos().tool_call_repo.get_by_session_id(session_id)]
    return api_response(data={"items": items, "count": len(items)})


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
    now = time.time()
    row = AgentSessionDB(
        id=sid or str(uuid.uuid4()),
        task_id=task_id,
        team_id=str(getattr(task, "team_id", "") or "") or None,
        share_session_id=sid or None,
        session_kind="agent_execution",
        title=str(session.get("title") or task.title or "Task Session").strip() or "Task Session",
        mode=str(session.get("mode") or "relay"),
        transport=str(session.get("transport") or "hub_relay"),
        owner_user_id=user_id,
        permissions=dict(session.get("permissions") or permissions or {}),
        status="running",
        created_at=float(session.get("created_at") or now),
        updated_at=now,
        started_at=now,
        expires_at=session.get("expires_at"),
    )
    saved = _repos().agent_session_repo.save(row)
    snapshot = PolicySnapshotDB(
        session_id=str(saved.id),
        task_id=task_id,
        policy_version="v1",
        risk_level="medium",
        allowed_tools_json=["read_file"],
        denied_tools_json=["network_write", "shell_root"],
        allowed_paths_json=["/agent/**", "/frontend-angular/**"],
        denied_paths_json=["/.env", "/secrets/**", "/**/*.pem", "/**/*.key"],
        cloud_allowed=False,
        runtime_boundary="local-only",
        requires_human_approval=True,
        approval_reason="bootstrap_session_requires_explicit_approval",
        created_at=now,
        updated_at=now,
    )
    persisted_snapshot = _repos().policy_snapshot_repo.save(snapshot)
    saved.policy_snapshot_id = str(persisted_snapshot.id)
    saved.updated_at = now
    saved = _repos().agent_session_repo.save(saved)
    bootstrap_tool_call = ToolCallDB(
        session_id=str(saved.id),
        task_id=task_id,
        action_id=f"approve:{saved.id}",
        tool_name="session_bootstrap",
        status="require_approval",
        risk_level="medium",
        target_path=None,
        created_at=now,
        updated_at=now,
        arguments_preview="bootstrap action for newly created session",
        arguments_hash=hashlib.sha256(b"bootstrap action for newly created session").hexdigest(),
    )
    _repos().tool_call_repo.save(bootstrap_tool_call)

    log_audit("control_center_task_session_created", {"task_id": task_id, "session_id": saved.id, "actor": user_id})
    return api_response(data={"session": _agent_session_item(saved)}, code=201)


@control_center_api_bp.route("/sessions/<session_id>/cancel", methods=["POST"])
@check_auth
def cancel_session(session_id: str):
    """B09: POST /api/sessions/{sessionId}/cancel"""
    user_id = _user_id()
    persisted = _repos().agent_session_repo.get_by_id(session_id)
    if persisted is None:
        return api_response(status="error", message="session_not_found", code=404)
    if str(persisted.owner_user_id or "") != user_id:
        return api_response(status="error", message="forbidden", code=403)

    if persisted.share_session_id:
        ok, reason = get_share_session_service().revoke_session(session_id=str(persisted.share_session_id), actor_user_id=user_id)
        if not ok and reason not in {"session_not_found"}:
            return api_response(status="error", message=reason or "cancel_failed", code=400)

    now = time.time()
    persisted.status = "cancelled"
    persisted.cancelled_at = now
    persisted.updated_at = now
    _repos().agent_session_repo.save(persisted)
    log_audit("control_center_session_cancelled", {"session_id": session_id, "actor": user_id})
    return api_response(data={"session_id": session_id, "status": "cancelled", "cancelled_at": now})


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

    tool_calls = _repos().tool_call_repo.get_by_session_id(session_id)
    for tc in tool_calls:
        status = str(getattr(tc, "status", "") or "").strip().lower()
        if status not in {"require_approval", "denied", "allowed"}:
            continue
        items.append(
            {
                "id": str(getattr(tc, "id", "") or ""),
                "decision": "require_approval" if status == "require_approval" else ("deny" if status == "denied" else "allow"),
                "decision_type": "tool_call_gate",
                "reason": f"tool_call:{getattr(tc, 'tool_name', '')}",
                "matched_rule_ids": [],
                "created_at": float(getattr(tc, "created_at", 0.0) or 0.0),
                "action_id": str(getattr(tc, "action_id", "") or ""),
                "tool_call_id": str(getattr(tc, "id", "") or ""),
            }
        )

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
    if not session_id:
        return api_response(status="error", message="session_id_required", code=400)

    actor = _user_id()
    session = _repos().agent_session_repo.get_by_id(session_id)
    if session is None:
        return api_response(status="error", message="session_not_found", code=404)
    if str(session.owner_user_id or "") != actor:
        return api_response(status="error", message="forbidden", code=403)

    tool_call = _repos().tool_call_repo.get_by_id(tool_call_id)
    if tool_call is None or str(tool_call.session_id or "") != session_id:
        return api_response(status="error", message="pending_action_not_found", code=404)
    if str(tool_call.action_id or "") != action_id:
        return api_response(status="error", message="pending_action_not_found", code=404)
    current_status = str(tool_call.status or "").strip().lower()
    if current_status in {"completed", "denied", "cancelled", "failed"}:
        return api_response(status="error", message="pending_action_not_approvable", code=409)
    if current_status not in {"require_approval", "proposed"}:
        return api_response(status="error", message="pending_action_not_approvable", code=409)

    approved_at = time.time()
    tool_call.status = "allowed"
    tool_call.approved_by_user_id = actor
    tool_call.updated_at = float(approved_at)
    _repos().tool_call_repo.save(tool_call)
    event = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "action_id": action_id,
        "tool_call_id": tool_call_id,
        "actor": actor,
        "created_at": approved_at,
    }
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


@control_center_api_bp.route("/policies", methods=["GET"])
@check_auth
def list_policies():
    """B13: GET /api/policies with versions and active flag."""
    rows = _repos().context_access_policy_repo.find_by_scope("system_default") or []
    latest_by_policy_id: dict[str, int] = {}
    for row in rows:
        pid = str(getattr(row, "policy_id", "") or "")
        ver = int(getattr(row, "version", 0) or 0)
        if pid and ver > latest_by_policy_id.get(pid, 0):
            latest_by_policy_id[pid] = ver

    items = []
    for row in rows:
        policy_id = str(getattr(row, "policy_id", "") or "")
        version = int(getattr(row, "version", 0) or 0)
        items.append(
            {
                "id": str(getattr(row, "id", "") or ""),
                "policy_id": policy_id,
                "version": version,
                "scope": str(getattr(row, "scope", "") or ""),
                "state": "active" if version == latest_by_policy_id.get(policy_id, -1) else "inactive",
                "active": version == latest_by_policy_id.get(policy_id, -1),
                "updated_at": float(getattr(row, "updated_at", 0.0) or 0.0),
            }
        )
    items.sort(key=lambda x: (str(x.get("policy_id") or ""), int(x.get("version") or 0)), reverse=True)
    return api_response(data={"items": items, "count": len(items)})


@control_center_api_bp.route("/codecompass/context-scopes", methods=["GET"])
@check_auth
def list_context_scopes():
    """B15: GET /api/codecompass/context-scopes"""
    defaults = [
        {"id": "repo_all", "label": "Repository (alles)", "include": ["/"], "exclude": []},
        {"id": "source_only", "label": "Nur Source", "include": ["/agent/**", "/frontend-angular/**"], "exclude": ["/tests/**"]},
        {"id": "safe_local", "label": "Safe Local", "include": ["/agent/**", "/frontend-angular/**"], "exclude": ["/.env", "/secrets/**", "/data/**"]},
    ]
    return api_response(data={"items": defaults, "count": len(defaults)})


@control_center_api_bp.route("/codecompass/context-scopes/preview", methods=["POST"])
@check_auth
def preview_context_scope():
    """B16: POST /api/codecompass/context-scopes/preview"""
    body = request.get_json(silent=True) or {}
    include = [str(item).strip() for item in list(body.get("include") or []) if str(item).strip()]
    exclude = [str(item).strip() for item in list(body.get("exclude") or []) if str(item).strip()]
    if not include:
        include = ["/"]
    sensitive = ["/.env", "/secrets/**", "/data/**", "/**/*.pem", "/**/*.key"]
    include_set = {str(item).strip() for item in include}
    exclude_set = {str(item).strip() for item in exclude}
    excluded_sensitive = [item for item in sensitive if item in exclude_set]
    if "/.env" not in excluded_sensitive:
        excluded_sensitive.append("/.env")
    include_warning = any(item in {"/", "/**", "**"} for item in include)
    included_nodes_estimate = max(1, len(include) * 120)
    excluded_nodes_estimate = len(exclude_set) * 25 + len(excluded_sensitive) * 10
    cloud_eligible = not any(item in include_set for item in ["/.env", "/secrets/**", "/data/**"])
    if include_warning:
        cloud_eligible = False
    return api_response(
        data={
            "scope_preview": {
                "include": include,
                "exclude": exclude,
                "excluded_sensitive_paths": sorted(set(excluded_sensitive)),
                "cloud_boundary_hint": "local-only recommended when sensitive paths are in scope",
                "warnings": ["include_scope_too_broad"] if include_warning else [],
                "included_count": int(included_nodes_estimate),
                "excluded_count": int(excluded_nodes_estimate),
                "cloud_eligible": bool(cloud_eligible),
            }
        }
    )


@control_center_api_bp.route("/events/stream", methods=["GET"])
@check_auth
def stream_control_center_events():
    """B17: GET /api/events/stream central SSE feed."""
    _ensure_event_poller()
    actor = _user_id()
    project_id_filter = str(request.args.get("project_id") or "").strip()
    session_id_filter = str(request.args.get("session_id") or "").strip()
    claim_project = str((getattr(g, "user", {}) or {}).get("stream_project_id") or "").strip()
    claim_session = str((getattr(g, "user", {}) or {}).get("stream_session_id") or "").strip()
    claim_is_stream = bool((getattr(g, "user", {}) or {}).get("cc_stream") is True)
    if claim_is_stream:
        if project_id_filter and claim_project and project_id_filter != claim_project:
            return api_response(status="error", message="forbidden", code=403)
        if session_id_filter and claim_session and session_id_filter != claim_session:
            return api_response(status="error", message="forbidden", code=403)
        if not project_id_filter and claim_project:
            project_id_filter = claim_project
        if not session_id_filter and claim_session:
            session_id_filter = claim_session
    if session_id_filter:
        session = _repos().agent_session_repo.get_by_id(session_id_filter)
        if session is None:
            return api_response(status="error", message="session_not_found", code=404)
        if str(getattr(session, "owner_user_id", "") or "") != actor:
            return api_response(status="error", message="forbidden", code=403)
    last_event_id_req = str(request.headers.get("Last-Event-ID") or request.args.get("last_event_id") or "").strip()

    def generate():
        last_event_id = last_event_id_req
        cursor = 0
        with _EVENT_LOCK:
            if last_event_id:
                for idx, item in enumerate(_EVENT_LOG):
                    if str(item.get("id") or "") == last_event_id:
                        cursor = idx + 1
                        break
            else:
                cursor = len(_EVENT_LOG)
        while True:
            now = time.time()
            batch: list[dict[str, Any]] = []
            with _EVENT_COND:
                if cursor >= len(_EVENT_LOG):
                    _EVENT_COND.wait(timeout=5.0)
                if cursor < len(_EVENT_LOG):
                    batch = _EVENT_LOG[cursor:]
                    cursor = len(_EVENT_LOG)
            for event in batch:
                payload = dict(event.get("payload") or {})
                if project_id_filter:
                    if str(event.get("type") or "") == "task_updated":
                        if str(payload.get("project_id") or "") != project_id_filter:
                            continue
                    elif str(event.get("type") or "") == "policy_decision":
                        decision_task_id = str(payload.get("task_id") or "")
                        if decision_task_id:
                            linked_task = _repos().task_repo.get_by_id(decision_task_id)
                            if linked_task is None or str(getattr(linked_task, "team_id", "") or "") != project_id_filter:
                                continue
                if session_id_filter and str(payload.get("session_id") or "") != session_id_filter:
                    continue
                yield f"id: {event['id']}\n"
                yield f"data: {json.dumps(event)}\n\n"
            yield f"data: {json.dumps({'id': _next_event_id(), 'channel': 'system', 'type': 'heartbeat', 'timestamp': now, 'payload': {}})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@control_center_api_bp.route("/events/stream-token", methods=["POST"])
@check_auth
def create_stream_token():
    body = request.get_json(silent=True) or {}
    actor = _user_id()
    user_payload = dict(getattr(g, "user", {}) or {})
    if not actor or not user_payload:
        return api_response(status="error", message="forbidden", code=403)
    project_id = str(body.get("project_id") or "").strip() or None
    session_id = str(body.get("session_id") or "").strip() or None
    if session_id:
        session = _repos().agent_session_repo.get_by_id(session_id)
        if session is None:
            return api_response(status="error", message="session_not_found", code=404)
        if str(getattr(session, "owner_user_id", "") or "") != actor:
            return api_response(status="error", message="forbidden", code=403)
    issued_at = int(time.time())
    expires_at = issued_at + 120
    token_payload = {
        "sub": actor,
        "role": str(user_payload.get("role") or "user"),
        "cc_stream": True,
        "stream_project_id": project_id,
        "stream_session_id": session_id,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(token_payload, settings.secret_key, algorithm="HS256")
    return api_response(data={"stream_token": token, "expires_at": expires_at, "ttl_seconds": 120})
