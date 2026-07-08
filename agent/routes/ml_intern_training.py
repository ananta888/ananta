from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.ml_intern_training_job_service import get_training_job_service

ml_intern_training_bp = Blueprint("ml_intern_training", __name__, url_prefix="/api/ml-intern-training")


@ml_intern_training_bp.route("/jobs", methods=["POST"])
@check_auth
@admin_required
def submit_training_job():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    service = get_training_job_service(cfg.get("ml_intern_training") or {})
    result = service.submit_job(payload)
    code = 202 if result.status in {"dry_run_completed", "completed", "trained"} else 400
    if result.status == "disabled":
        code = 403
    return api_response(data=result.to_dict(), code=code)
