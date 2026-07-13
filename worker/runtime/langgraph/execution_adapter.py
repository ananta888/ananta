"""Shared ExecutionRuntimePort adapter for one delegated LangGraph node."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.ports import (
    DelegatedExecutionRequest,
    DelegatedExecutionResult,
    RuntimeValidationReport,
)
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_EXECUTION_CAPABILITIES,
    LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
    LANGGRAPH_HUB_NODE_RESULT_SCHEMA,
)


class LangGraphSingleNodeAdapterPort(Protocol):
    def execute(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        resume_token: str | None = None,
    ) -> Any: ...


class LangGraphExecutionRuntimeAdapter:
    """Validate a neutral plan and execute exactly its Hub-selected node."""

    runtime_id = "langgraph"
    capabilities = LANGGRAPH_EXECUTION_CAPABILITIES

    def __init__(self, adapter: LangGraphSingleNodeAdapterPort) -> None:
        self._adapter = adapter

    def validate(self, plan: ExecutionPlan) -> RuntimeValidationReport:
        reasons = [issue.code for issue in plan.validate()]
        unsupported = tuple(sorted(set(plan.capabilities) - set(self.capabilities)))
        reasons.extend(
            f"langgraph_capability_unsupported:{value}" for value in unsupported
        )
        reasons.extend(
            f"langgraph_node_type_unsupported:{node.node_type}"
            for node in plan.nodes
            if node.node_type not in {"task", "merge", "checkpoint", "component"}
        )
        return RuntimeValidationReport(
            runtime_id=self.runtime_id,
            valid=not reasons,
            reason_codes=tuple(sorted(set(reasons))),
            unsupported_capabilities=unsupported,
        )

    def execute(
        self,
        request: DelegatedExecutionRequest,
    ) -> DelegatedExecutionResult:
        try:
            plan = _bound_plan(request)
            node = next(
                (value for value in plan.nodes if value.node_id == request.step_id),
                None,
            )
            if node is None:
                raise ValueError("langgraph_step_binding_mismatch")
            if node.node_type in {"merge", "component"}:
                raise ValueError("langgraph_hub_owned_node_type")
            parameters = dict(request.parameters)
            raw_payload = parameters.get("payload") or {}
            if not isinstance(raw_payload, Mapping):
                raise ValueError("langgraph_execution_payload_invalid")
            payload = {
                **dict(raw_payload),
                "schema": LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
                "execution_scope": "single_hub_node",
                "execution_plan": plan.to_dict(),
                "delegated_node_id": request.step_id,
                "tenant_id": request.tenant_id,
                "workflow_id": request.workflow_id,
                "run_id": request.run_id,
                "step_id": request.step_id,
                "plan_hash": request.plan_hash,
                "policy_version": request.policy_version,
                "authorization_envelope": dict(request.authorization_envelope),
                "attempt_id": request.attempt_id,
                "fencing_token": request.fencing_token,
            }
            task_id = str(parameters.get("hub_task_id") or "").strip()
            if not task_id:
                raise ValueError("langgraph_hub_task_id_required")
            result = self._adapter.execute(
                task_id=task_id,
                task_type=str(parameters.get("task_type") or "agent_workflow"),
                payload=payload,
                resume_token=None,
            )
            artifact = _node_artifact(result, step_id=request.step_id)
            return DelegatedExecutionResult(
                runtime_id=self.runtime_id,
                run_id=request.run_id,
                step_id=request.step_id,
                attempt_id=request.attempt_id,
                fencing_token=request.fencing_token,
                status=str(artifact.get("status") or "failed"),
                artifact_refs=tuple(
                    sorted(str(value) for value in dict(artifact.get("artifacts") or {}).values())
                ),
                reason_code=str(artifact.get("reason_code") or ""),
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


def _bound_plan(request: DelegatedExecutionRequest) -> ExecutionPlan:
    raw = request.parameters.get("execution_plan")
    if not isinstance(raw, Mapping):
        raise ValueError("langgraph_execution_plan_required")
    plan = ExecutionPlan.from_mapping(dict(raw))
    if (
        plan.tenant_id != request.tenant_id
        or plan.workflow_id != request.workflow_id
        or plan.plan_hash != request.plan_hash
        or plan.policy_version != request.policy_version
    ):
        raise ValueError("langgraph_execution_plan_binding_mismatch")
    envelope = RuntimeAuthorizationEnvelope.from_mapping(
        request.authorization_envelope
    )
    if (
        envelope.tenant_id != request.tenant_id
        or envelope.workflow_id != request.workflow_id
        or envelope.run_id != request.run_id
        or envelope.step_id != request.step_id
        or envelope.plan_hash != request.plan_hash
        or envelope.policy_version != request.policy_version
    ):
        raise ValueError("langgraph_authorization_binding_mismatch")
    return plan


def _node_artifact(result: Any, *, step_id: str) -> dict[str, Any]:
    artifacts = getattr(result, "artifacts", None)
    if not isinstance(artifacts, list):
        raise ValueError("langgraph_node_result_missing")
    matches = [
        dict(value)
        for value in artifacts
        if isinstance(value, Mapping)
        and value.get("schema") == LANGGRAPH_HUB_NODE_RESULT_SCHEMA
        and value.get("node_id") == step_id
    ]
    if len(matches) != 1:
        raise ValueError("langgraph_node_result_binding_mismatch")
    return matches[0]


def _reason(exc: Exception) -> str:
    value = str(exc).strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,159}", value):
        return value
    return "langgraph_execution_request_invalid"


__all__ = ["LangGraphExecutionRuntimeAdapter", "LangGraphSingleNodeAdapterPort"]
