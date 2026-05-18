from __future__ import annotations

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.planning_dataset_export_service import get_planning_dataset_export_service

planning_dataset_bp = Blueprint("planning_dataset_admin", __name__)


@planning_dataset_bp.route("/admin/planning/dataset", methods=["GET"])
@check_auth
@admin_required
def planning_dataset_export():
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    min_generated_tasks = request.args.get("min_generated_tasks")
    data = get_planning_dataset_export_service().export(
        limit=max(1, min(limit, 5000)),
        model_provider=request.args.get("model_provider"),
        model_name=request.args.get("model_name"),
        prompt_version=request.args.get("prompt_version"),
        min_generated_tasks=int(min_generated_tasks) if str(min_generated_tasks or "").strip() else None,
        include_raw_output=bool(str(request.args.get("include_raw_output") or "").strip() in {"1", "true", "yes"}),
        output_format=str(request.args.get("format") or "json"),
    )
    return api_response(data=data)
