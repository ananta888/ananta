from __future__ import annotations

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.backend_observability_service import get_backend_observability_service


backend_observability_bp = Blueprint("debug_backend_observability", __name__)


@backend_observability_bp.route("/debug/backend-observability", methods=["GET"])
@check_auth
def backend_observability_summary():
    try:
        lookback_seconds = int(request.args.get("lookback_seconds") or 3600)
    except (TypeError, ValueError):
        lookback_seconds = 3600
    try:
        trace_limit = int(request.args.get("trace_limit") or 800)
    except (TypeError, ValueError):
        trace_limit = 800

    data = get_backend_observability_service().summary(
        lookback_seconds=lookback_seconds,
        trace_limit=trace_limit,
    )
    return api_response(data=data, code=200)

