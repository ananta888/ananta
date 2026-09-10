"""Bind the existing Worker route and nested verification to one Pi result."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.pi_native_result_validation import validate_pi_native_result
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand, NativeNodeResult
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.workflow_adapter_task import WorkflowAdapterTaskResult

_ROUTE_FIELDS = frozenset({
    "status", "output", "exit_code", "reason_code", "adapter_kind", "artifacts", "sources",
    "workflow_adapter_verification",
})


@dataclass(frozen=True, slots=True)
class PiNativeResultCandidate:
    result_json: str
    adapter_result_json: str
    digest: str

    @property
    def result(self) -> NativeNodeResult:
        return NativeNodeResult.from_mapping(json.loads(self.result_json))

    @property
    def adapter_result(self) -> WorkflowAdapterTaskResult:
        return WorkflowAdapterTaskResult.from_mapping(json.loads(self.adapter_result_json))


def pi_native_result_candidate(
    response: Mapping[str, Any], *, command: NativeNodeCommand, hub_task_id: str,
) -> PiNativeResultCandidate:
    """Validate facts only; the caller must supply current Hub authority."""
    reason = "pi_native_result_envelope_invalid"
    if not isinstance(response, Mapping) or set(response) - {"handler_contract"} != _ROUTE_FIELDS:
        raise ValueError(reason)
    verification = response["workflow_adapter_verification"]
    if (
        not isinstance(verification, Mapping)
        or set(verification) != {"schema", "workflow_adapter_task_result"}
        or verification["schema"] != "ananta.workflow-adapter-task-verification.v1"
    ):
        raise ValueError(reason)
    outer = verification["workflow_adapter_task_result"]
    if not isinstance(outer, dict):
        raise ValueError(reason)
    nested = outer.get("adapter_result")
    if not isinstance(nested, dict) or "verification" not in nested:
        raise ValueError(reason)
    witness = nested["verification"]
    if (
        not isinstance(witness, dict) or set(witness) != {"schema", "native_node_result"}
        or witness["schema"] != "ananta.native_graph_task_verification.v1"
    ):
        raise ValueError(reason)
    native = {key: value for key, value in nested.items() if key != "verification"}
    result = validate_pi_native_result(native, command=command, hub_task_id=hub_task_id)
    witness_result = validate_pi_native_result(
        witness["native_node_result"], command=command, hub_task_id=hub_task_id,
    )
    if native_context_digest(result.to_dict()) != native_context_digest(witness_result.to_dict()):
        raise ValueError("pi_native_result_verification_conflict")
    try:
        parsed = WorkflowAdapterTaskResult.from_mapping(outer)
        if native_context_digest(outer) != native_context_digest(parsed.to_dict()):
            raise ValueError(reason)
    except (TypeError, ValueError) as exc:
        raise ValueError(reason) from exc
    status = {"completed": "success", "cancelled": "cancelled"}.get(result.status, "failed")
    if (
        parsed.hub_task_id != hub_task_id or parsed.adapter_kind != "native" or parsed.status != status
        or parsed.reason_code != result.reason_code or parsed.artifacts or parsed.sources
        or response["status"] != result.status or response["adapter_kind"] != "native"
        or response["reason_code"] != result.reason_code
        or response["artifacts"] != [] or response["sources"] != []
        or type(response["exit_code"]) is not int
        or response["exit_code"] != (0 if result.status == "completed" else 1)
        or response["output"] != (parsed.summary or parsed.reason_code)
    ):
        raise ValueError(reason)
    return PiNativeResultCandidate(
        result_json=json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        adapter_result_json=json.dumps(parsed.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        digest=native_context_digest(outer),
    )
