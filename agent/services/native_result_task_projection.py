"""Canonical Native command/result facts from persisted Task fields."""

from collections.abc import Mapping

from agent.common.native_task_result_binding import NATIVE_RESULT_RECEIPT
from agent.services.native_graph_node_evaluation import NativeGraphNodeEvaluator
from agent.services.workflow_runtime.components import validate_compiled_component_output
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand, NativeNodeResult
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.workflow_adapter_task import WorkflowAdapterTaskResult


def native_task_command(task):
    try:
        context = task.worker_execution_context or {}
        raw = context.get("native_node_command")
        command = NativeNodeCommand.from_mapping(raw)
        if (
            context.get("schema") != "ananta.native_graph_worker_context.v1"
            or context.get("runtime_path") != "native_graph_node"
            or task.task_kind != command.node.task_kind
            or task.derivation_reason != "native_graph_hub_delegation"
            or task.tenant_id != command.tenant_id or not task.id
            or native_context_digest(raw) != native_context_digest(command.to_dict())
        ):
            raise ValueError("binding")
        return command
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("native_result_task_binding_invalid") from exc


def native_task_result(task, command):
    verification = task.verification_status or {}
    outer = verification.get("workflow_adapter_task_result")
    raw = verification.get("native_node_result")
    if not isinstance(outer, dict) or not isinstance(raw, dict):
        raise ValueError("native_result_required")
    result = NativeNodeResult.from_mapping(raw)
    if (
        type(raw.get("fencing_token")) is not int
        or native_context_digest(raw) != native_context_digest(result.to_dict())
    ):
        raise ValueError("native_result_contract_invalid")
    expected = {
        "command_id": command.command_id, "hub_task_id": task.id, "tenant_id": command.tenant_id,
        "workflow_id": command.workflow_id, "run_id": command.run_id, "node_id": command.node.node_id,
        "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
    }
    if any(getattr(result, field) != value for field, value in expected.items()) or result.status != task.status:
        raise ValueError("native_node_result_binding_mismatch")
    parsed = WorkflowAdapterTaskResult.from_mapping(outer)
    nested = outer.get("adapter_result")
    witness = nested.get("verification") if isinstance(nested, Mapping) else None
    if (
        not isinstance(witness, Mapping)
        or witness.get("schema") != "ananta.native_graph_task_verification.v1"
        or native_context_digest(witness.get("native_node_result")) != native_context_digest(raw)
        or native_context_digest({k: v for k, v in nested.items() if k != "verification"}) != native_context_digest(raw)
        or native_context_digest(parsed.to_dict()) != native_context_digest(outer)
        or parsed.hub_task_id != task.id or parsed.adapter_kind != "native"
        or parsed.status != {"completed": "success", "cancelled": "cancelled"}.get(result.status, "failed")
        or parsed.reason_code != result.reason_code
        or task.last_output != (parsed.summary or parsed.reason_code)
        or type(task.last_exit_code) is not int or task.last_exit_code != (0 if result.status == "completed" else 1)
    ):
        raise ValueError("native_result_envelope_invalid")
    if result.status == "completed":
        validate_compiled_component_output(command.node, result.output_data)
        NativeGraphNodeEvaluator.validate_artifacts(command.node, result)
    return result


def native_result_digest(task):
    return native_context_digest({
        "status": task.status, "output": task.last_output, "exit_code": task.last_exit_code,
        "verification": {k: v for k, v in (task.verification_status or {}).items() if k in {
            "native_node_result", "workflow_adapter_task_result", "execution_artifacts",
        }},
    })


def require_native_result_receipt(task, command):
    receipt = (task.verification_status or {}).get(NATIVE_RESULT_RECEIPT)
    if (
        not isinstance(receipt, dict) or receipt.get("schema") != "ananta.native-result-receipt.v1"
        or receipt.get("classification") != "technical_observation"
        or receipt.get("hub_task_id") != task.id or receipt.get("tenant_id") != task.tenant_id
        or receipt.get("project_id") != task.project_id
        or receipt.get("command_digest") != native_context_digest(command.to_dict())
        or receipt.get("result_digest") != native_result_digest(task)
    ):
        raise ValueError("native_result_receipt_required")
    return native_task_result(task, command)
