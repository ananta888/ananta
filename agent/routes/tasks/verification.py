from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.verification_service import get_verification_service

verification_bp = Blueprint("tasks_verification", __name__)


@verification_bp.route("/tasks/<tid>/verification", methods=["GET"])
@check_auth
def task_verification(tid: str):
    from agent.repository import task_repo

    task = task_repo.get_by_id(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    records = get_verification_service().ensure_task_spec(tid)
    return api_response(
        data={
            "task_id": tid,
            "verification_spec": records or {},
            "verification_status": dict(task.verification_status or {}),
        }
    )
