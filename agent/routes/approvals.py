"""ALWA-009: Flask API for the persistent approval lifecycle.

- ``GET /api/approvals?status=pending`` lists requests (filters: status,
  task_id, goal_id). Responses carry digest prefixes and scope summaries,
  never raw arguments or content payloads.
- ``POST /api/approvals/<id>/decision`` accepts ``decision=granted|denied``
  with optional ``reason`` and bounded ``expires_at`` override.

Auth follows the existing route pattern (``@check_auth``). Errors:
400 invalid decision/expires_at, 404 unknown request, 409 already
decided/expired.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from agent.auth import check_auth
from agent.services.approval_request_service import (
    ApprovalDecisionError,
    digest_prefix,
    get_approval_request_service,
)

approvals_bp = Blueprint("approvals", __name__)

_SCOPE_SUMMARY_KEYS = {"approval_class", "pre_approval", "goal_id", "source", "reason_code"}


def _request_to_payload(row) -> dict[str, Any]:
    return {
        "request_id": row.id,
        "task_id": row.task_id,
        "goal_id": row.goal_id,
        "trace_id": row.trace_id,
        "tool_name": row.tool_name,
        "digest_prefix": digest_prefix(row.arguments_digest),
        "target_fingerprint_prefix": digest_prefix(row.target_fingerprint),
        "risk_class": row.risk_class,
        "k_class": row.k_class,
        "governance_mode": row.governance_mode,
        "status": row.status,
        "scope_summary": {k: v for k, v in dict(row.scope or {}).items() if k in _SCOPE_SUMMARY_KEYS},
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "decided_at": row.decided_at,
        "decided_by": row.decided_by,
        "decision_reason": row.decision_reason,
        "consumed_at": row.consumed_at,
        "has_content_payload": bool(row.content_artifact_ref),
    }


@approvals_bp.get("/api/approvals")
@check_auth
def list_approvals():
    svc = get_approval_request_service()
    svc.expire_old_requests()
    status = str(request.args.get("status") or "").strip() or None
    task_id = str(request.args.get("task_id") or "").strip() or None
    goal_id = str(request.args.get("goal_id") or "").strip() or None
    rows = svc.list_requests(status=status, task_id=task_id, goal_id=goal_id)
    return jsonify({"requests": [_request_to_payload(row) for row in rows]})


@approvals_bp.get("/api/approvals/<request_id>")
@check_auth
def get_approval(request_id: str):
    row = get_approval_request_service().get_request(request_id)
    if row is None:
        return jsonify({"error": "request_not_found"}), 404
    return jsonify(_request_to_payload(row))


@approvals_bp.post("/api/approvals/<request_id>/decision")
@check_auth
def decide_approval(request_id: str):
    body = request.get_json(silent=True) or {}
    decision = str(body.get("decision") or "").strip().lower()
    reason = str(body.get("reason") or "").strip() or None
    expires_at = body.get("expires_at")
    decided_by = str((getattr(g, "user", {}) or {}).get("sub") or (getattr(g, "user", {}) or {}).get("username") or "operator")
    try:
        row = get_approval_request_service().decide_request(
            request_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            expires_at=expires_at,
        )
    except ApprovalDecisionError as exc:
        return jsonify({"error": exc.code}), exc.http_status
    return jsonify(_request_to_payload(row))
