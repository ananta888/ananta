"""Adapter from the shared ExecutionRuntimePort to one Native worker node."""

from __future__ import annotations

import re

from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.ports import (
    DelegatedExecutionRequest,
    DelegatedExecutionResult,
    RuntimeValidationReport,
)
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime


class NativeExecutionRuntimeAdapter:
    runtime_id = "ananta-native"
    capabilities = frozenset(
        {
            "approval",
            "bounded_parallel",
            "checkpoint",
            "deterministic_merge",
            "resume",
            "retrieval",
            "structured_output",
            "subgraphs",
            "tool_calling",
        }
    )

    def __init__(self, node_runtime: NativeDelegatedNodeRuntime) -> None:
        self._node_runtime = node_runtime

    def validate(self, plan: ExecutionPlan) -> RuntimeValidationReport:
        reasons = [issue.code for issue in plan.validate()]
        unsupported = tuple(sorted(set(plan.capabilities) - set(self.capabilities)))
        reasons.extend(f"native_capability_unsupported:{value}" for value in unsupported)
        reasons.extend(
            f"native_node_type_unsupported:{node.node_type}"
            for node in plan.nodes
            if node.node_type not in {"task", "merge", "checkpoint", "component"}
        )
        return RuntimeValidationReport(
            runtime_id=self.runtime_id,
            valid=not reasons,
            reason_codes=tuple(sorted(set(reasons))),
            unsupported_capabilities=unsupported,
        )

    def execute(self, request: DelegatedExecutionRequest) -> DelegatedExecutionResult:
        parameters = dict(request.parameters)
        try:
            command = NativeNodeCommand.from_mapping(
                {
                    "command_id": parameters.get("command_id"),
                    "control_task_id": parameters.get("control_task_id"),
                    "tenant_id": request.tenant_id,
                    "workflow_id": request.workflow_id,
                    "run_id": request.run_id,
                    "plan_hash": request.plan_hash,
                    "policy_version": request.policy_version,
                    "node": parameters.get("node"),
                    "authorization": request.authorization_envelope,
                    "attempt_id": request.attempt_id,
                    "fencing_token": request.fencing_token,
                    "input_data": parameters.get("input_data") or {},
                    "artifact_refs": parameters.get("artifact_refs") or {},
                    "operation_id": parameters.get("operation_id") or "",
                    "side_effect_revision": parameters.get("side_effect_revision") or 0,
                    "provider_binding": parameters.get("provider_binding"),
                }
            )
            _assert_request_artifacts(request, command)
            hub_task_id = str(parameters.get("hub_task_id") or "").strip()
            if not hub_task_id:
                raise ValueError("native_hub_task_id_required")
            result = self._node_runtime.execute(command, hub_task_id=hub_task_id)
            return DelegatedExecutionResult(
                runtime_id=self.runtime_id,
                run_id=result.run_id,
                step_id=result.node_id,
                attempt_id=result.attempt_id,
                fencing_token=result.fencing_token,
                status=result.status,
                artifact_refs=tuple(sorted(result.artifact_refs.values())),
                reason_code=result.reason_code,
            )
        except Exception as exc:
            return DelegatedExecutionResult(
                runtime_id=self.runtime_id,
                run_id=request.run_id,
                step_id=request.step_id,
                attempt_id=request.attempt_id,
                fencing_token=request.fencing_token,
                status="failed",
                reason_code=_reason(exc),
            )


def _assert_request_artifacts(
    request: DelegatedExecutionRequest, command: NativeNodeCommand
) -> None:
    if set(request.input_artifact_refs) != set(command.artifact_refs.values()):
        raise ValueError("native_input_artifact_binding_mismatch")
    if command.node.node_id != request.step_id:
        raise ValueError("native_step_binding_mismatch")
    envelope = RuntimeAuthorizationEnvelope.from_mapping(request.authorization_envelope)
    if envelope.step_id != request.step_id:
        raise ValueError("native_authorization_step_binding_mismatch")


def _reason(exc: Exception) -> str:
    value = str(exc).strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,159}", value):
        return value
    return "native_execution_request_invalid"
