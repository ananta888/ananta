"""Run-status document and plan/binding helpers of the LangGraph control bridge.

Split out of ``langgraph_workflow_control_bridge`` (SRP): the pure parts of
the Hub-owned LangGraph run state -- the persisted status document (initial
shape, control events, settling, revision bumps), node-result projection,
plan lookups and the principal/run binding assertions.  The bridge keeps
scheduling, queue submission and command handling and delegates here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from agent.services.workflow_adapter_task_queue_service import WorkflowAdapterQueueError
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_service import WorkflowPrincipal
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_HUB_NODE_RESULT_SCHEMA,
    validate_langgraph_node_result,
)

LANGGRAPH_RUNTIME_ID = "langgraph"
TERMINAL_STEP_STATES = frozenset({"completed", "failed", "cancelled", "skipped"})
ACTIVE_STEP_STATES = frozenset({"created", "queued", "running", "assigned", "in_progress"})


def node_outcome(task: Mapping[str, Any], *, node_id: str, plan_hash: str) -> dict[str, Any]:
    task_status = str(task.get("status") or "failed").lower()
    result = task.get("result")
    reason = str(task.get("reason_code") or "")
    if isinstance(result, Mapping):
        reason = str(result.get("reason_code") or reason)
        adapter_result = result.get("adapter_result")
        if isinstance(adapter_result, Mapping):
            for artifact in adapter_result.get("artifacts") or ():
                if not isinstance(artifact, Mapping):
                    continue
                if artifact.get("schema") == LANGGRAPH_HUB_NODE_RESULT_SCHEMA:
                    validate_langgraph_node_result(artifact)
                    if str(artifact.get("node_id") or "") != node_id:
                        raise WorkflowAdapterQueueError("langgraph_node_result_binding_mismatch", status_code=409)
                    if str(artifact.get("plan_hash") or "") != plan_hash:
                        raise WorkflowAdapterQueueError(
                            "langgraph_node_result_plan_binding_mismatch",
                            status_code=409,
                        )
                    return {
                        "status": str(artifact.get("status") or "failed"),
                        "reason_code": str(artifact.get("reason_code") or reason),
                        "value": artifact.get("value"),
                        "artifacts": dict(artifact.get("artifacts") or {}),
                        "tokens": int(artifact.get("tokens") or 0),
                        "cost_micros": int(artifact.get("cost_micros") or 0),
                    }
    return {
        "status": "cancelled" if task_status == "cancelled" else "failed",
        "reason_code": reason or "langgraph_node_result_missing",
    }


def initial_run_status(
    binding: WorkflowControlRunBinding,
    *,
    plan: ExecutionPlan,
    runtime_id: str,
    clock: Callable[[], float],
) -> dict[str, Any]:
    metadata = binding.request.metadata
    raw_input = metadata.get("input_data") or metadata.get("parameters") or {}
    value = {
        "schema": "ananta.workflow_backend_status.v1",
        "backend": runtime_id,
        "runtime_id": runtime_id,
        "runtime_version": "1.0.0",
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "hub_task_id": binding.run_id,
        "status": "created",
        "reason": "",
        "reason_code": "",
        "revision": 0,
        "checkpoint_ref": binding.checkpoint_id,
        "plan_hash": plan.plan_hash,
        "paused": False,
        "approved_gates": [],
        "workflow_input": dict(raw_input) if isinstance(raw_input, Mapping) else {},
        "steps": [
            {
                "id": node.node_id,
                "step_id": node.node_id,
                "task_kind": node.task_kind,
                "gate_id": node.gate_id,
                "status": "pending",
                "reason_code": "",
                "hub_task_id": "",
                "retry": 0,
                "value": None,
                "artifacts": {},
            }
            for node in plan.nodes
        ],
        "events": [],
        "updated_at": float(clock()),
    }
    append_control_event(
        value,
        runtime_id=runtime_id,
        clock=clock,
        event_type="workflow.run.started",
        reason_code="hub_control_started",
    )
    return value


def settle_run_status(status: dict[str, Any]) -> None:
    states = {str(step.get("status") or "pending") for step in status["steps"]}
    if status.get("paused"):
        status.update(status="paused", reason_code="workflow_paused")
    elif states <= {"completed", "skipped"}:
        status.update(status="completed", reason="", reason_code="")
    elif "cancelled" in states and not states.intersection(ACTIVE_STEP_STATES):
        status.update(status="cancelled", reason_code="workflow_cancelled")
    elif "failed" in states and not states.intersection(ACTIVE_STEP_STATES | {"pending", "waiting_for_approval"}):
        status.update(status="failed", reason_code="langgraph_node_failed")
    elif "waiting_for_approval" in states and not states.intersection(ACTIVE_STEP_STATES):
        status.update(status="waiting_for_approval", reason_code="approval_required")
    elif states.intersection(ACTIVE_STEP_STATES):
        status.update(status="running", reason="", reason_code="")
    else:
        status.update(status="running", reason="", reason_code="")


def bump_run_status(status: dict[str, Any], *, plan: ExecutionPlan, clock: Callable[[], float]) -> dict[str, Any]:
    value = deepcopy(status)
    revision = int(value.get("revision") or 0) + 1
    value["revision"] = revision
    value["checkpoint_ref"] = f"langgraph:{plan.plan_hash}:{revision}"
    value["updated_at"] = float(clock())
    return value


def append_control_event(
    status: dict[str, Any],
    *,
    runtime_id: str,
    clock: Callable[[], float],
    event_type: str,
    reason_code: str,
    step_id: str = "",
    hub_task_id: str = "",
) -> None:
    values = list(status.get("events") or ())
    identity = len(values) + 1
    values.append(
        {
            "event_id": f"lg-control:{status.get('run_id')}:{identity}",
            "workflow_id": str(status.get("workflow_id") or ""),
            "run_id": str(status.get("run_id") or ""),
            "step_id": step_id,
            "event_type": event_type,
            "timestamp": float(clock()),
            "details": {
                "runtime_id": runtime_id,
                "reason_code": reason_code,
                "hub_task_id": hub_task_id,
            },
        }
    )
    status["events"] = values[-256:]


def incoming_edges(plan: ExecutionPlan) -> dict[str, tuple[Any, ...]]:
    return {
        node.node_id: tuple(
            sorted(
                (edge for edge in plan.edges if edge.target == node.node_id),
                key=lambda edge: edge.source,
            )
        )
        for node in plan.nodes
    }


def parallel_limits(
    binding: WorkflowControlRunBinding,
    *,
    plan: ExecutionPlan,
) -> tuple[int, int, int]:
    metadata = binding.request.metadata
    raw = (
        plan.metadata.get("parallel_limit", 4),
        metadata.get("tenant_parallel_limit", 4),
        metadata.get("worker_parallel_limit", 4),
    )
    try:
        values = tuple(int(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("langgraph_parallel_limit_invalid") from exc
    if any(value < 1 or value > 64 for value in values):
        raise ValueError("langgraph_parallel_limit_invalid")
    return values  # type: ignore[return-value]


def plan_node(plan: ExecutionPlan, node_id: str) -> ExecutionNode:
    node = next((value for value in plan.nodes if value.node_id == node_id), None)
    if node is None:
        raise ValueError("workflow_control_step_binding_mismatch")
    return node


def status_step(status: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    step = next(
        (value for value in status.get("steps") or () if str(value.get("step_id") or "") == str(node_id)),
        None,
    )
    if not isinstance(step, dict):
        raise ValueError("workflow_control_step_binding_mismatch")
    return step


def assert_principal_binding(
    binding: WorkflowControlRunBinding,
    principal: WorkflowPrincipal,
) -> None:
    if binding.tenant_id != principal.tenant_id or binding.subject_id != principal.subject_id:
        raise PermissionError("workflow_control_principal_binding_mismatch")


def assert_run_binding(
    binding: WorkflowControlRunBinding,
    *,
    principal: WorkflowPrincipal,
    run_id: str,
    plan: ExecutionPlan,
) -> None:
    assert_principal_binding(binding, principal)
    if binding.runtime_id != LANGGRAPH_RUNTIME_ID:
        raise PermissionError("workflow_control_runtime_binding_mismatch")
    if binding.run_id != str(run_id) or binding.plan_hash != plan.plan_hash:
        raise PermissionError("workflow_control_run_binding_mismatch")
    if binding.policy_version != plan.policy_version:
        raise PermissionError("workflow_control_policy_binding_mismatch")


__all__ = [
    "ACTIVE_STEP_STATES",
    "LANGGRAPH_RUNTIME_ID",
    "TERMINAL_STEP_STATES",
    "append_control_event",
    "assert_principal_binding",
    "assert_run_binding",
    "bump_run_status",
    "incoming_edges",
    "initial_run_status",
    "node_outcome",
    "parallel_limits",
    "plan_node",
    "settle_run_status",
    "status_step",
]
