from flask import Blueprint, current_app, g

from agent.auth import check_auth
from agent.common.sgpt import run_llm_cli_command
from agent.common.errors import api_response
from agent.llm_integration import _call_llm
from agent.metrics import TASK_COMPLETED, TASK_FAILED
from agent.models import TaskStepExecuteRequest, TaskStepProposeRequest
from agent.routes.tasks.utils import _forward_to_worker
from agent.services.service_registry import get_core_services
from agent.tools import registry as tool_registry
from agent.utils import validate_request

execution_bp = Blueprint("tasks_execution", __name__)


def _services():
    return get_core_services()


def _respond(outcome) -> object:
    if outcome.status == "success":
        return api_response(data=outcome.data, code=outcome.code)
    return api_response(status=outcome.status, message=outcome.message, data=outcome.data, code=outcome.code)


@execution_bp.route("/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def propose_step():
    """
    Nächsten Schritt vorschlagen (LLM)
    ---
    responses:
      200:
        description: Vorschlag erhalten
    """
    data: TaskStepProposeRequest = g.validated_data
    cfg = current_app.config["AGENT_CONFIG"]

    return api_response(
        data=_services().task_execution_service.propose_direct_step(
            data,
            agent_cfg=cfg,
            provider_urls=current_app.config["PROVIDER_URLS"],
            openai_api_key=current_app.config["OPENAI_API_KEY"],
            agent_name=current_app.config["AGENT_NAME"],
            llm_caller=_call_llm,
        )
    )


@execution_bp.route("/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    """
    Vorgeschlagenen Schritt ausführen
    ---
    responses:
      200:
        description: Schritt ausgeführt
    """
    data: TaskStepExecuteRequest = g.validated_data
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    response_payload = _services().task_execution_service.execute_direct_step(
        data,
        agent_cfg=agent_cfg,
        agent_name=current_app.config["AGENT_NAME"],
    )
    if response_payload["status"] == "completed":
        TASK_COMPLETED.inc()
    else:
        TASK_FAILED.inc()
    return api_response(data=response_payload)


@execution_bp.route("/tasks/<tid>/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def task_propose(tid):
    """
    Vorschlag für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Vorschlag erhalten
    """
    data: TaskStepProposeRequest = g.validated_data
    outcome = _services().task_scoped_execution_service.propose_task_step(
        tid,
        data,
        cli_runner=run_llm_cli_command,
        forwarder=_forward_to_worker,
        tool_definitions_resolver=tool_registry.get_tool_definitions,
    )
    return _respond(outcome)


@execution_bp.route("/tasks/<tid>/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def task_execute(tid):
    """
    Ausführung für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Schritt ausgeführt
    """
    data: TaskStepExecuteRequest = g.validated_data
    outcome = _services().task_scoped_execution_service.execute_task_step(
        tid,
        data,
        forwarder=_forward_to_worker,
        cli_runner=run_llm_cli_command,
        tool_definitions_resolver=tool_registry.get_tool_definitions,
    )
    return _respond(outcome)
