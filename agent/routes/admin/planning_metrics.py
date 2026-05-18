from __future__ import annotations

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.planning_metrics_service import get_planning_metrics_service

planning_metrics_bp = Blueprint("planning_metrics_admin", __name__)


@planning_metrics_bp.route("/admin/planning/metrics", methods=["GET"])
@check_auth
@admin_required
def planning_metrics():
    data = get_planning_metrics_service().summarize(
        model_provider=request.args.get("model_provider"),
        model_name=request.args.get("model_name"),
        prompt_version=request.args.get("prompt_version"),
        output_shape=request.args.get("output_shape"),
        behavior_profile_name=request.args.get("behavior_profile_name"),
    )
    return api_response(data=data)
