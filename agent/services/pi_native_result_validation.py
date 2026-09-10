"""Strict Pi candidate facts, separate from Hub lease/evidence admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand, NativeNodeResult

_FIELDS = frozenset({
    "schema", "result_id", "command_id", "hub_task_id", "tenant_id", "workflow_id", "run_id", "node_id",
    "attempt_id", "fencing_token", "status", "output_data", "artifact_refs", "budget_usage", "reason_code",
    "side_effect_status",
})
_IDENTIFIERS = ("result_id", "command_id", "hub_task_id", "tenant_id", "workflow_id", "run_id", "node_id", "attempt_id")


def validate_pi_native_result(
    raw: Mapping[str, Any], *, command: NativeNodeCommand, hub_task_id: str, task_status: str | None = None,
) -> NativeNodeResult:
    """Validate without coercing wire values or interpreting correlations as evidence.

    Callers still own authoritative Task loading and current assignment/lease
    verification. A valid return is not a release or authentication decision.
    """
    command.assert_valid()
    if command.node.task_kind != "pi_coding_agent" or command.provider_binding is None:
        raise ValueError("pi_native_result_command_required")
    if not isinstance(raw, Mapping) or set(raw) != _FIELDS:
        raise ValueError("pi_native_result_contract_invalid")
    expected = {
        "command_id": command.command_id, "hub_task_id": hub_task_id, "tenant_id": command.tenant_id,
        "workflow_id": command.workflow_id, "run_id": command.run_id, "node_id": command.node.node_id,
        "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
    }
    if type(raw["fencing_token"]) is not int or any(raw[key] != value for key, value in expected.items()):
        raise ValueError("pi_native_result_binding_mismatch")
    if task_status is not None and raw["status"] != task_status:
        raise ValueError("pi_native_result_terminal_mismatch")
    if (
        raw["schema"] != "ananta.native_node_result.v1"
        or any(not _identifier(raw[key]) for key in _IDENTIFIERS)
        or not isinstance(raw["reason_code"], str) or not isinstance(raw["status"], str)
        or raw["status"] not in {"completed", "failed", "cancelled"}
        or raw["artifact_refs"] != {} or raw["budget_usage"] != {} or raw["side_effect_status"] != ""
        or not _valid_output(raw, command.provider_binding.model_id)
    ):
        raise ValueError("pi_native_result_contract_invalid")
    try:
        return NativeNodeResult.from_mapping(dict(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("pi_native_result_contract_invalid") from exc


def _identifier(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(char) < 33 for char in value)


def _valid_output(raw: Mapping[str, Any], model_id: str) -> bool:
    output, completed = raw["output_data"], raw["status"] == "completed"
    if not isinstance(output, dict) or (completed and raw["reason_code"]) or (not completed and not raw["reason_code"]):
        return False
    if not completed and output == {}:
        return True  # The Native runtime may reject before entering the provider.
    if set(output) != {"provider_id", "model_id", "output", "exit_code"}:
        return False
    return (
        output["provider_id"] == "pi" and output["model_id"] == model_id
        and _bounded_text(output["output"])
        and (completed or output["output"] == "")
        and type(output["exit_code"]) is int and -255 <= output["exit_code"] <= 255
        and (not completed or output["exit_code"] == 0)
    )


def _bounded_text(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 16_384:
        return False
    try:
        return len(value.encode("utf-8")) <= 65_536
    except UnicodeEncodeError:
        return False
