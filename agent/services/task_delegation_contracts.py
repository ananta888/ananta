"""Data contracts exchanged by the Hub task-delegation services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DelegationRequest:
    task_id: str
    parent_task: dict[str, Any]
    data: Any


@dataclass(frozen=True)
class RoutingDecision:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TaskDelegationPlan:
    agent_url: str
    selected_by_policy: bool
    selection: Any
    policy_decision: Any
    routing_hint: dict[str, Any] | None
    effective_task_kind: str | None
    effective_required_capabilities: list[str]
    preferred_backend: str | None
    worker_runtime_decision: Any = None


@dataclass(frozen=True)
class WorkerExecutionBundle:
    subtask_id: str
    context_bundle: Any
    context_policy: dict[str, Any]
    retrieval_hints: dict[str, Any]
    task_neighborhood: dict[str, Any]
    expected_output_schema: dict[str, Any]
    allowed_tools: list[str]
    routing_decision: RoutingDecision
    worker_job: Any
    workspace_scope: dict[str, Any]
    worker_execution_context: dict[str, Any]
    delegation_payload: dict[str, Any]
