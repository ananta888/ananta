"""Pure validation/state helpers for signed LangGraph plan replacement."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan


def replacement_plan(command: SignedWorkflowCommand) -> ExecutionPlan:
    raw = command.payload.get("replacement_plan")
    if not isinstance(raw, Mapping):
        raise ValueError("langgraph_plan_artifact_resolver_required")
    replacement = ExecutionPlan.from_mapping(dict(raw))
    replacement.assert_valid()
    if replacement.plan_hash != str(command.payload.get("replacement_plan_hash") or ""):
        raise ValueError("langgraph_replacement_plan_hash_mismatch")
    return replacement


def assert_safe_plan_edit(
    current: ExecutionPlan,
    replacement: ExecutionPlan,
    status: Mapping[str, Any],
) -> None:
    if (
        replacement.tenant_id != current.tenant_id
        or replacement.workflow_id != current.workflow_id
    ):
        raise ValueError("langgraph_plan_edit_binding_mismatch")
    if replacement.policy_version != current.policy_version:
        raise ValueError("langgraph_plan_edit_policy_change_denied")
    if set(replacement.capabilities) - set(current.capabilities):
        raise ValueError("langgraph_plan_edit_capability_escalation")
    current_nodes = {node.node_id: node for node in current.nodes}
    replacement_nodes = {node.node_id: node for node in replacement.nodes}
    immutable = {
        str(step.get("step_id") or "")
        for step in status.get("steps") or ()
        if step.get("status") in {"completed", "skipped"}
    }
    for node_id in immutable:
        if (
            node_id not in current_nodes
            or node_id not in replacement_nodes
            or current_nodes[node_id].to_dict() != replacement_nodes[node_id].to_dict()
        ):
            raise ValueError("langgraph_plan_edit_executed_node_changed")


def replace_status_plan(
    status: dict[str, Any],
    *,
    current: ExecutionPlan,
    replacement: ExecutionPlan,
) -> None:
    previous_steps = {
        str(step.get("step_id") or ""): deepcopy(step)
        for step in status.get("steps") or ()
    }
    current_nodes = {node.node_id: node for node in current.nodes}
    rebuilt: list[dict[str, Any]] = []
    for node in replacement.nodes:
        previous = previous_steps.get(node.node_id)
        if (
            previous is not None
            and previous.get("status") in {"completed", "skipped"}
            and node.node_id in current_nodes
            and current_nodes[node.node_id].to_dict() == node.to_dict()
        ):
            rebuilt.append(previous)
            continue
        rebuilt.append(_pending_step(node))
    status.update(
        effective_plan=replacement.to_dict(),
        plan_hash=replacement.plan_hash,
        steps=rebuilt,
        approved_gates=[],
        reason_code="",
    )


def _pending_step(node: Any) -> dict[str, Any]:
    return {
        "id": node.node_id,
        "step_id": node.node_id,
        "task_kind": node.task_kind,
        "gate": bool(node.gate_id),
        "gate_id": node.gate_id,
        "status": "pending",
        "reason_code": "",
        "hub_task_id": "",
        "retry": 0,
        "value": None,
        "artifacts": {},
    }


__all__ = ["assert_safe_plan_edit", "replace_status_plan", "replacement_plan"]
