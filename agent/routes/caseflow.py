"""CaseFlow Core REST API — generic case management endpoints.

POST   /api/caseflow/cases                      — create case
GET    /api/caseflow/cases                      — list cases (filter: case_type, status, priority, risk, search, limit, offset)
GET    /api/caseflow/cases/<case_id>            — get case
PATCH  /api/caseflow/cases/<case_id>            — update case fields
POST   /api/caseflow/cases/<case_id>/transition — status transition
GET    /api/caseflow/cases/<case_id>/timeline   — timeline events
GET    /api/caseflow/cases/<case_id>/artifacts  — list artifacts
POST   /api/caseflow/cases/<case_id>/artifacts  — add artifact
GET    /api/caseflow/cases/<case_id>/actions    — list actions
POST   /api/caseflow/cases/<case_id>/actions    — add action
GET    /api/caseflow/actions/open               — all open actions across cases
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from agent.caseflow.actions import CaseAction, next_action
from agent.caseflow.artifacts import CaseArtifact
from agent.caseflow.models import CaseFlowCase
from agent.caseflow.status_machine import get_status_machine
from agent.caseflow.timeline import CaseEvent, append_event, get_events_for_case

caseflow_bp = Blueprint("caseflow", __name__, url_prefix="/api/caseflow")

# In-memory stores (dict-based, sufficient for tests and PoC)
_cases: dict[str, CaseFlowCase] = {}
_artifacts: dict[str, list[CaseArtifact]] = {}  # case_id -> list
_actions: dict[str, list[CaseAction]] = {}       # case_id -> list


def _case_to_dict(case: CaseFlowCase) -> dict[str, Any]:
    data = case.model_dump()
    data["created_at"] = case.created_at.isoformat()
    data["updated_at"] = case.updated_at.isoformat()
    if case.closed_at:
        data["closed_at"] = case.closed_at.isoformat()
    return data


def _artifact_to_dict(a: CaseArtifact) -> dict[str, Any]:
    data = a.model_dump()
    data["created_at"] = a.created_at.isoformat()
    data["artifact_kind"] = a.artifact_kind.value
    data["status"] = a.status.value
    return data


def _action_to_dict(a: CaseAction) -> dict[str, Any]:
    data = a.model_dump()
    data["created_at"] = a.created_at.isoformat()
    data["status"] = a.status.value
    if a.due_at:
        data["due_at"] = a.due_at.isoformat()
    if a.completed_at:
        data["completed_at"] = a.completed_at.isoformat()
    return data


def _event_to_dict(e: CaseEvent) -> dict[str, Any]:
    data = e.model_dump()
    data["created_at"] = e.created_at.isoformat()
    return data


@caseflow_bp.route("/cases", methods=["POST"])
def create_case():
    body = request.get_json(silent=True) or {}
    if not body.get("case_type") or not body.get("title"):
        return jsonify({"error": "case_type and title are required"}), 400
    case = CaseFlowCase(
        case_type=body["case_type"],
        title=body["title"],
        status=body.get("status", "new"),
        priority=body.get("priority", "medium"),
        risk=body.get("risk", "low"),
        owner=body.get("owner"),
        source=body.get("source"),
        domain_payload=body.get("domain_payload") or {},
        metadata=body.get("metadata") or {},
    )
    _cases[case.id] = case
    _artifacts[case.id] = []
    _actions[case.id] = []
    # Timeline: case_created
    evt = CaseEvent(
        case_id=case.id,
        event_type="case_created",
        title=f"Case erstellt: {case.title}",
        payload={"case_type": case.case_type, "status": case.status},
    )
    append_event(case.id, evt)
    return jsonify(_case_to_dict(case)), 201


@caseflow_bp.route("/cases", methods=["GET"])
def list_cases():
    case_type = request.args.get("case_type")
    status = request.args.get("status")
    priority = request.args.get("priority")
    risk = request.args.get("risk")
    search = request.args.get("search", "").lower()
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))

    results = [c for c in _cases.values() if not c.is_deleted]
    if case_type:
        results = [c for c in results if c.case_type == case_type]
    if status:
        results = [c for c in results if c.status == status]
    if priority:
        results = [c for c in results if c.priority == priority]
    if risk:
        results = [c for c in results if c.risk == risk]
    if search:
        results = [c for c in results if search in c.title.lower()]

    total = len(results)
    page = results[offset: offset + limit]
    return jsonify({"items": [_case_to_dict(c) for c in page], "total": total}), 200


@caseflow_bp.route("/cases/<case_id>", methods=["GET"])
def get_case(case_id: str):
    case = _cases.get(case_id)
    if not case or case.is_deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_case_to_dict(case)), 200


@caseflow_bp.route("/cases/<case_id>", methods=["PATCH"])
def update_case(case_id: str):
    case = _cases.get(case_id)
    if not case or case.is_deleted:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    allowed_fields = {"title", "priority", "risk", "owner", "source", "domain_payload", "metadata"}
    for field_name, value in body.items():
        if field_name in allowed_fields:
            setattr(case, field_name, value)
    case.updated_at = datetime.utcnow()
    _cases[case_id] = case
    return jsonify(_case_to_dict(case)), 200


@caseflow_bp.route("/cases/<case_id>/transition", methods=["POST"])
def transition_case(case_id: str):
    case = _cases.get(case_id)
    if not case or case.is_deleted:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    to_status = body.get("to_status")
    actor = body.get("actor", "user")
    reason = body.get("reason")
    if not to_status:
        return jsonify({"error": "to_status is required"}), 400

    machine = get_status_machine(case.case_type) or get_status_machine("generic")
    assert machine is not None
    result = machine.validate_transition(case.status, to_status, actor=actor)

    if not result.valid:
        return jsonify({"ok": False, "error_code": result.error_code, "detail": result.error_detail}), 422

    old_status = case.status
    case.status = to_status
    case.updated_at = datetime.utcnow()
    if to_status in (machine.terminal_statuses or []):
        case.closed_at = datetime.utcnow()
    _cases[case_id] = case

    # Timeline event
    evt = CaseEvent(
        case_id=case_id,
        event_type="status_changed",
        actor_type="user" if actor != "system" else "system",
        actor_id=actor,
        title=f"Status: {old_status} → {to_status}",
        payload={"from_status": old_status, "to_status": to_status, "reason": reason},
    )
    append_event(case_id, evt)
    return jsonify({"ok": True, "status": to_status}), 200


@caseflow_bp.route("/cases/<case_id>/timeline", methods=["GET"])
def get_timeline(case_id: str):
    if case_id not in _cases:
        return jsonify({"error": "not_found"}), 404
    events = get_events_for_case(case_id)
    return jsonify([_event_to_dict(e) for e in events]), 200


@caseflow_bp.route("/cases/<case_id>/artifacts", methods=["GET"])
def list_artifacts(case_id: str):
    if case_id not in _cases:
        return jsonify({"error": "not_found"}), 404
    return jsonify([_artifact_to_dict(a) for a in _artifacts.get(case_id, [])]), 200


@caseflow_bp.route("/cases/<case_id>/artifacts", methods=["POST"])
def add_artifact(case_id: str):
    if case_id not in _cases:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    if not body.get("artifact_type") or not body.get("title"):
        return jsonify({"error": "artifact_type and title are required"}), 400
    artifact = CaseArtifact(
        case_id=case_id,
        artifact_type=body["artifact_type"],
        title=body["title"],
        source=body.get("source", "manual"),
        content_text=body.get("content_text"),
        trace_id=body.get("trace_id"),
        agent_run_id=body.get("agent_run_id"),
        is_sensitive=body.get("is_sensitive", False),
    )
    _artifacts.setdefault(case_id, []).append(artifact)
    evt = CaseEvent(
        case_id=case_id,
        event_type="artifact_added",
        title=f"Artefakt hinzugefügt: {artifact.title}",
        payload={"artifact_id": artifact.id, "artifact_type": artifact.artifact_type},
        artifact_id=artifact.id,
    )
    append_event(case_id, evt)
    return jsonify(_artifact_to_dict(artifact)), 201


@caseflow_bp.route("/cases/<case_id>/actions", methods=["GET"])
def list_actions(case_id: str):
    if case_id not in _cases:
        return jsonify({"error": "not_found"}), 404
    return jsonify([_action_to_dict(a) for a in _actions.get(case_id, [])]), 200


@caseflow_bp.route("/cases/<case_id>/actions", methods=["POST"])
def add_action(case_id: str):
    if case_id not in _cases:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    if not body.get("action_type") or not body.get("title"):
        return jsonify({"error": "action_type and title are required"}), 400
    action = CaseAction(
        case_id=case_id,
        action_type=body["action_type"],
        title=body["title"],
        description=body.get("description"),
        priority=body.get("priority", "medium"),
        blocking=body.get("blocking", False),
        assigned_to=body.get("assigned_to"),
    )
    _actions.setdefault(case_id, []).append(action)
    evt = CaseEvent(
        case_id=case_id,
        event_type="action_created",
        title=f"Aktion erstellt: {action.title}",
        payload={"action_id": action.id, "action_type": action.action_type},
    )
    append_event(case_id, evt)
    return jsonify(_action_to_dict(action)), 201


@caseflow_bp.route("/actions/open", methods=["GET"])
def open_actions():
    result = []
    for case_id, actions in _actions.items():
        for a in actions:
            if a.status.value == "open":
                result.append(_action_to_dict(a))
    return jsonify(result), 200


def reset_stores() -> None:
    """Reset in-memory stores. For tests only."""
    _cases.clear()
    _artifacts.clear()
    _actions.clear()
    from agent.caseflow.timeline import clear_events
    clear_events()
