from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.planning_review_queue_service import get_planning_review_queue_service

planning_review_bp = Blueprint("planning_review_admin", __name__)


@planning_review_bp.route("/admin/planning/review-queue", methods=["GET"])
@check_auth
@admin_required
def planning_review_queue_list():
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    items = get_planning_review_queue_service().list_open(limit=max(1, min(limit, 2000)))
    return api_response(data={"count": len(items), "items": [item.model_dump() for item in items]})


@planning_review_bp.route("/admin/planning/review-queue/<item_id>/action", methods=["POST"])
@check_auth
@admin_required
def planning_review_queue_action(item_id: str):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip()
    if not action:
        return api_response(status="error", message="action_required", code=400)
    actor = str((getattr(g, "user", {}) or {}).get("sub") or "admin")
    item = get_planning_review_queue_service().apply_action(
        item_id=item_id,
        action=action,
        actor=actor,
        details=dict(payload.get("details") or {}),
    )
    if item is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=item.model_dump())
