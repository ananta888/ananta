from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

verification_bp = Blueprint("tasks_verification", __name__)


def _services():
    return get_core_services()


def _repos():
    return get_repository_registry()


@verification_bp.route("/tasks/<tid>/verification", methods=["GET"])
@check_auth
def task_verification(tid: str):
    task = _repos().task_repo.get_by_id(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    records = _services().verification_service.ensure_task_spec(tid)
    return api_response(
        data={
            "task_id": tid,
            "verification_spec": records or {},
            "verification_status": dict(task.verification_status or {}),
        }
    )
