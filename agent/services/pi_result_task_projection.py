"""Canonical Pi command and result facts from persisted Task fields."""

from agent.services.pi_native_result_envelope import pi_native_result_candidate
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from ananta_contracts.native_context_bundle import native_context_digest


def pi_task_command(task) -> NativeNodeCommand:
    try:
        context = task.worker_execution_context or {}
        raw = context.get("native_node_command")
        command = NativeNodeCommand.from_mapping(raw)
        if (
            context.get("schema") != "ananta.native_graph_worker_context.v1"
            or context.get("runtime_path") != "native_graph_node"
            or task.task_kind != "pi_coding_agent" or command.node.task_kind != "pi_coding_agent"
            or task.derivation_reason != "native_graph_hub_delegation"
            or task.tenant_id != command.tenant_id or not task.id
            or native_context_digest(raw) != native_context_digest(command.to_dict())
        ):
            raise ValueError("binding")
        return command
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("pi_native_result_task_binding_invalid") from exc


def pi_task_result_candidate(task, command):
    verification = task.verification_status or {}
    outer = verification.get("workflow_adapter_task_result")
    if not isinstance(outer, dict):
        raise ValueError("pi_native_result_required")
    candidate = pi_native_result_candidate({
        "status": task.status, "output": task.last_output, "exit_code": task.last_exit_code,
        "reason_code": outer.get("reason_code"), "adapter_kind": outer.get("adapter_kind"),
        "artifacts": outer.get("artifacts"), "sources": outer.get("sources"),
        "workflow_adapter_verification": {
            "schema": "ananta.workflow-adapter-task-verification.v1", "workflow_adapter_task_result": outer,
        },
    }, command=command, hub_task_id=task.id)
    stored_native = native_context_digest(verification.get("native_node_result"))
    if stored_native != native_context_digest(candidate.result.to_dict()):
        raise ValueError("pi_native_result_verification_conflict")
    if verification.get("execution_artifacts", []) != []:
        raise ValueError("pi_native_result_artifacts_unsupported")
    return candidate
