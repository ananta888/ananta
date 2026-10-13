"""Check original Native response facts before generic forwarding side effects."""

from types import SimpleNamespace

from agent.common.native_task_result_binding import is_native_task, task_value
from agent.common.pi_task_result_binding import is_pi_task
from agent.services.native_result_task_projection import native_task_command, native_task_result
from ananta_contracts.native_context_bundle import native_context_digest


def validate_forwarded_native_result(*, task_id, dispatched_task, response, load_task):
    authoritative = load_task(task_id)
    if not (is_native_task(authoritative) or is_native_task(dispatched_task)) or (
        is_pi_task(authoritative) or is_pi_task(dispatched_task)
    ):
        return
    if authoritative is None:
        raise ValueError("native_result_task_missing")
    command = native_task_command(authoritative)
    context = task_value(dispatched_task, "worker_execution_context") or {}
    if (
        task_value(dispatched_task, "id") != task_id or authoritative.id != task_id
        or task_value(dispatched_task, "assigned_agent_url") != authoritative.assigned_agent_url
        or native_context_digest(context.get("native_node_command")) != native_context_digest(command.to_dict())
    ):
        raise ValueError("native_result_dispatch_projection_mismatch")
    fields = {"status", "output", "exit_code", "reason_code", "adapter_kind", "artifacts", "sources",
              "workflow_adapter_verification"}
    if not isinstance(response, dict) or set(response) - {"handler_contract"} != fields:
        raise ValueError("native_result_envelope_invalid")
    witness = response["workflow_adapter_verification"]
    if not isinstance(witness, dict) or witness.get("schema") != "ananta.workflow-adapter-task-verification.v1":
        raise ValueError("native_result_envelope_invalid")
    outer = witness.get("workflow_adapter_task_result")
    nested = outer.get("adapter_result") if isinstance(outer, dict) else None
    native = nested.get("verification") if isinstance(nested, dict) else None
    if not isinstance(native, dict):
        raise ValueError("native_result_required")
    candidate = SimpleNamespace(**{
        name: getattr(authoritative, name) for name in (
            "id", "tenant_id", "task_kind", "derivation_reason", "worker_execution_context",
        )
    })
    candidate.status, candidate.last_output, candidate.last_exit_code = (
        response["status"], response["output"], response["exit_code"],
    )
    candidate.verification_status = {
        "workflow_adapter_task_result": outer, "native_node_result": native.get("native_node_result"),
    }
    result = native_task_result(candidate, command)
    if (
        response["adapter_kind"] != "native" or response["reason_code"] != result.reason_code
        or response["artifacts"] != outer.get("artifacts") or response["sources"] != outer.get("sources")
    ):
        raise ValueError("native_result_envelope_invalid")
