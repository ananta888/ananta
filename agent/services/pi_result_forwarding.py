"""Validate the original Pi Worker response before generic field projection."""

from agent.common.pi_task_result_binding import is_pi_task, task_value
from agent.services.pi_native_result_envelope import pi_native_result_candidate
from agent.services.pi_result_task_projection import pi_task_command
from ananta_contracts.native_context_bundle import native_context_digest


def validate_forwarded_pi_result(*, task_id, dispatched_task, response, load_task) -> None:
    authoritative = load_task(task_id)
    if not is_pi_task(authoritative) and not is_pi_task(dispatched_task):
        return
    if authoritative is None:
        raise ValueError("pi_native_result_task_missing")
    command = pi_task_command(authoritative)
    dispatched_context = task_value(dispatched_task, "worker_execution_context") or {}
    if (
        task_value(dispatched_task, "id") != task_id or authoritative.id != task_id
        or task_value(dispatched_task, "assigned_agent_url") != authoritative.assigned_agent_url
        or native_context_digest(dispatched_context.get("native_node_command"))
        != native_context_digest(command.to_dict())
    ):
        raise ValueError("pi_native_result_dispatch_projection_mismatch")
    pi_native_result_candidate(response, command=command, hub_task_id=task_id)
