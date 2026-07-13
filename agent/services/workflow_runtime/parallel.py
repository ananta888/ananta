"""Hub-owned bounded fan-out selection and deterministic merge policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.workflow_runtime.execution_plan import (
    MERGE_PARTIAL_FAILURE_POLICIES,
    MERGE_STRATEGIES,
    ExecutionNode,
    ExecutionPlan,
)


@dataclass(frozen=True)
class DelegationCandidate:
    node_id: str
    task_kind: str
    required_capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class DelegationBatch:
    candidates: tuple[DelegationCandidate, ...]
    deferred_node_ids: tuple[str, ...]
    effective_limit: int


class ParallelCapacityPort(Protocol):
    def available_slots(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
    ) -> int: ...


class BoundedFanOutScheduler:
    """Select ready nodes; actual task creation remains an injected Hub concern."""

    def select_ready(
        self,
        plan: ExecutionPlan,
        *,
        completed_node_ids: set[str] | frozenset[str],
        running_node_ids: set[str] | frozenset[str] = frozenset(),
        failed_node_ids: set[str] | frozenset[str] = frozenset(),
        tenant_limit: int,
        worker_limit: int,
        plan_limit: int | None = None,
        run_id: str = "",
        capacity: ParallelCapacityPort | None = None,
    ) -> DelegationBatch:
        if tenant_limit < 1 or worker_limit < 1 or (plan_limit is not None and plan_limit < 1):
            raise ValueError("parallel_limit_invalid")
        completed = set(completed_node_ids)
        running = set(running_node_ids)
        failed = set(failed_node_ids)
        node_ids = {node.node_id for node in plan.nodes}
        if (completed | running | failed) - node_ids:
            raise ValueError("parallel_state_node_unknown")

        dependencies: dict[str, set[str]] = {node.node_id: set() for node in plan.nodes}
        for edge in plan.edges:
            dependencies[edge.target].add(edge.source)
        candidates = [
            node
            for node in plan.nodes
            if node.node_id not in completed | running | failed
            and dependencies[node.node_id].issubset(completed)
            and not dependencies[node.node_id].intersection(failed)
        ]
        candidates.sort(key=lambda node: node.node_id)
        raw_plan_limit = plan.metadata.get("parallel_limit")
        declared_plan_limit = (
            int(raw_plan_limit) if raw_plan_limit is not None else max(tenant_limit, worker_limit)
        )
        effective_plan_limit = min(
            value for value in (declared_plan_limit, plan_limit) if value is not None
        )
        if effective_plan_limit < 1:
            raise ValueError("parallel_limit_invalid")
        available = min(effective_plan_limit, tenant_limit, worker_limit)
        if capacity is not None:
            available = min(
                available,
                max(
                    0,
                    capacity.available_slots(
                        tenant_id=plan.tenant_id,
                        workflow_id=plan.workflow_id,
                        run_id=run_id,
                    ),
                ),
            )
        available = max(0, available - len(running))

        selected: list[ExecutionNode] = []
        nodes_by_id = {node.node_id: node for node in plan.nodes}
        group_counts: dict[str, int] = {}
        for node_id in running:
            running_node = nodes_by_id[node_id]
            running_group = str(running_node.metadata.get("parallel_group") or "default")
            group_counts[running_group] = group_counts.get(running_group, 0) + 1
        deferred: list[str] = []
        for node in candidates:
            group = str(node.metadata.get("parallel_group") or "default")
            group_limit = int(node.metadata.get("parallel_limit") or available or 1)
            if len(selected) >= available or group_counts.get(group, 0) >= group_limit:
                deferred.append(node.node_id)
                continue
            selected.append(node)
            group_counts[group] = group_counts.get(group, 0) + 1
        return DelegationBatch(
            candidates=tuple(
                DelegationCandidate(
                    node_id=node.node_id,
                    task_kind=node.task_kind,
                    required_capabilities=node.required_capabilities,
                    allowed_tools=node.allowed_tools,
                )
                for node in selected
            ),
            deferred_node_ids=tuple(deferred),
            effective_limit=available,
        )


@dataclass(frozen=True)
class BranchResult:
    node_id: str
    status: str
    value: Any = None
    reason_code: str = ""


@dataclass(frozen=True)
class MergeResult:
    status: str
    value: Any
    failed_branches: tuple[str, ...] = ()
    reason_code: str = ""


class DeterministicMergeService:
    def merge(
        self,
        results: tuple[BranchResult, ...] | list[BranchResult],
        *,
        strategy: str,
        partial_failure: str = "fail",
    ) -> MergeResult:
        if strategy not in MERGE_STRATEGIES:
            raise ValueError("merge_strategy_unsupported")
        if partial_failure not in MERGE_PARTIAL_FAILURE_POLICIES:
            raise ValueError("merge_partial_failure_policy_invalid")
        ordered = sorted(results, key=lambda result: result.node_id)
        if len({result.node_id for result in ordered}) != len(ordered):
            raise ValueError("merge_duplicate_branch")
        failed = tuple(result.node_id for result in ordered if result.status != "completed")
        if failed and partial_failure == "fail":
            return MergeResult(
                status="failed",
                value=None,
                failed_branches=failed,
                reason_code="merge_branch_failed",
            )
        successful = [result for result in ordered if result.status == "completed"]
        if strategy == "ordered-by-node-id":
            value: Any = [result.value for result in successful]
        elif strategy == "object-by-node-id":
            value = {result.node_id: result.value for result in successful}
        return MergeResult(
            status="completed",
            value=value,
            failed_branches=failed,
            reason_code="merge_partial" if failed else "merge_complete",
        )
