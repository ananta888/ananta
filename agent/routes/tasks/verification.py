from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.tasks.task_source_access import (
    authorized_task_source_payload,
)
from agent.services.service_registry import get_core_services
from agent.services.task_read_projection_service import (
    get_task_read_projection_service,
)

verification_bp = Blueprint("tasks_verification", __name__)


def _services():
    return get_core_services()


@verification_bp.route("/tasks/<tid>/verification", methods=["GET"])
@check_auth
def task_verification(tid: str):
    task, error = authorized_task_source_payload(tid)
    if error is not None:
        return error
    assert task is not None
    records = _services().verification_service.project_task_spec(tid)
    return api_response(
        data={
            "task_id": tid,
            "verification_spec": records or {},
            "verification_status": (
                get_task_read_projection_service().verification_detail(
                    task.get("verification_status")
                )
            ),
        }
    )
