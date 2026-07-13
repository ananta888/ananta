"""Pinned LangGraph execution for the framework-neutral ``ExecutionPlan``.

The Hub remains the orchestration authority.  This module compiles an already
delegated, validated plan into one local LangGraph invocation; it has no task
queue, worker registry, or worker-to-worker delegation dependency.  Scheduling
and merge decisions use the same neutral services as the Native runtime.
"""

from __future__ import annotations

import operator
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, TypedDict

from agent.services.workflow_runtime.components import validate_compiled_component_output
from agent.services.workflow_runtime.condition_evaluator import DeclarativeConditionEvaluator
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.parallel import (
    BoundedFanOutScheduler,
    BranchResult,
    DeterministicMergeService,
)
from worker.adapters.workflow_adapter_base import WorkerError
from worker.adapters.workflow_budget import WorkflowBudgetGuard

LANGGRAPH_EXECUTION_PLAN_RESULT_SCHEMA = "ananta.langgraph_execution_plan_result.v1"
_TERMINAL_NODE_STATUSES = frozenset({"blocked", "cancelled", "completed", "failed", "skipped"})


@dataclass(frozen=True)
class LangGraphParallelLimits:
    """Hub-bound upper limits; the plan may narrow them further."""

    plan: int
    tenant: int
    worker: int

    def assert_valid(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in self.as_tuple()):
            raise ValueError("langgraph_parallel_limit_invalid")

    def as_tuple(self) -> tuple[int, int, int]:
        return self.plan, self.tenant, self.worker

    @property
    def effective(self) -> int:
        self.assert_valid()
        return min(self.as_tuple())

    def to_dict(self) -> dict[str, int]:
        return {
            "plan": self.plan,
            "tenant": self.tenant,
            "worker": self.worker,
            "effective": self.effective,
        }


@dataclass(frozen=True)
class ExecutionPlanNodeRequest:
    plan: ExecutionPlan
    node: ExecutionNode
    workflow_input: dict[str, Any]
    dependency_results: dict[str, Any]
    execution_payload: dict[str, Any]


@dataclass(frozen=True)
class ExecutionPlanNodeOutcome:
    status: str
    value: Any = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""
    tokens: int = 0
    cost_micros: int = 0

    @classmethod
    def completed(
        cls,
        value: Any,
        *,
        artifacts: Mapping[str, Any] | None = None,
        tokens: int = 0,
        cost_micros: int = 0,
    ) -> "ExecutionPlanNodeOutcome":
        return cls(
            status="completed",
            value=value,
            artifacts=dict(artifacts or {}),
            tokens=tokens,
            cost_micros=cost_micros,
        )

    @classmethod
    def failed(cls, reason_code: str) -> "ExecutionPlanNodeOutcome":
        return cls(status="failed", reason_code=str(reason_code or "langgraph_plan_node_failed"))

    @classmethod
    def cancelled(cls, reason_code: str = "cancel_requested") -> "ExecutionPlanNodeOutcome":
        return cls(status="cancelled", reason_code=str(reason_code or "cancel_requested"))

    def assert_valid(self) -> None:
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("langgraph_plan_node_outcome_status_invalid")
        if self.status in {"failed", "cancelled"} and not self.reason_code:
            raise ValueError("langgraph_plan_node_outcome_reason_required")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.tokens, self.cost_micros)
        ):
            raise ValueError("langgraph_plan_node_outcome_budget_invalid")


class ExecutionPlanNodeExecutor(Protocol):
    """Worker-local execution seam; it cannot enqueue or delegate tasks."""

    def execute(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome: ...


@dataclass(frozen=True)
class LangGraphPlanNodeRecord:
    node_id: str
    status: str
    value: Any = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""
    failed_branches: tuple[str, ...] = ()
    tokens: int = 0
    cost_micros: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "value": self.value,
            "artifacts": {key: self.artifacts[key] for key in sorted(self.artifacts)},
            "reason_code": self.reason_code,
            "failed_branches": list(self.failed_branches),
            "tokens": self.tokens,
            "cost_micros": self.cost_micros,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LangGraphPlanNodeRecord":
        record = cls(
            node_id=str(raw.get("node_id") or ""),
            status=str(raw.get("status") or ""),
            value=raw.get("value"),
            artifacts=dict(raw.get("artifacts") or {}),
            reason_code=str(raw.get("reason_code") or ""),
            failed_branches=tuple(str(value) for value in raw.get("failed_branches") or ()),
            tokens=int(raw.get("tokens") or 0),
            cost_micros=int(raw.get("cost_micros") or 0),
        )
        if not record.node_id or record.status not in _TERMINAL_NODE_STATUSES:
            raise ValueError("langgraph_plan_node_record_invalid")
        return record


@dataclass(frozen=True)
class LangGraphExecutionPlanResult:
    status: str
    reason_code: str
    plan_hash: str
    records: tuple[LangGraphPlanNodeRecord, ...]
    batches: tuple[tuple[str, ...], ...]
    limits: LangGraphParallelLimits
    max_observed_parallelism: int
    elapsed_seconds: float
    approved_gates: tuple[str, ...] = ()

    @property
    def node_results(self) -> dict[str, Any]:
        return {
            record.node_id: record.value
            for record in self.records
            if record.status == "completed"
        }

    @property
    def artifacts(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for record in self.records:
            for artifact_id, value in record.artifacts.items():
                if artifact_id in values:
                    raise ValueError("langgraph_plan_duplicate_artifact")
                values[artifact_id] = value
        return {key: values[key] for key in sorted(values)}

    @property
    def failed_nodes(self) -> dict[str, str]:
        return {
            record.node_id: record.reason_code
            for record in self.records
            if record.status == "failed"
        }

    def canonical_trace(self) -> tuple[dict[str, Any], ...]:
        """Stable trace: concurrency completion order is intentionally excluded."""

        events: list[dict[str, Any]] = [
            {
                "event": "workflow.run.started",
                "plan_hash": self.plan_hash,
                "limits": self.limits.to_dict(),
            }
        ]
        events.extend(
            {"event": "workflow.approval.granted", "gate_id": gate_id}
            for gate_id in self.approved_gates
        )
        records = {record.node_id: record for record in self.records}
        for index, batch in enumerate(self.batches):
            events.append(
                {
                    "event": "workflow.batch.scheduled",
                    "batch": index,
                    "node_ids": list(batch),
                }
            )
            for node_id in batch:
                record = records.get(node_id)
                if record is not None:
                    events.append(
                        {
                            "event": f"workflow.step.{record.status}",
                            "node_id": node_id,
                            "reason_code": record.reason_code,
                            "failed_branches": list(record.failed_branches),
                        }
                    )
        events.append(
            {
                "event": f"workflow.run.{self.status}",
                "reason_code": self.reason_code,
            }
        )
        return tuple(events)

    def to_artifact(self) -> dict[str, Any]:
        return {
            "schema": LANGGRAPH_EXECUTION_PLAN_RESULT_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "plan_hash": self.plan_hash,
            "records": [record.to_dict() for record in self.records],
            "batches": [list(batch) for batch in self.batches],
            "limits": self.limits.to_dict(),
            "max_observed_parallelism": self.max_observed_parallelism,
            "approved_gates": list(self.approved_gates),
            "artifacts": self.artifacts,
            "trace": list(self.canonical_trace()),
        }


class _LangGraphPlanState(TypedDict):
    workflow_input: dict[str, Any]
    records: Annotated[list[dict[str, Any]], operator.add]


class _ActivityCounter:
    def __init__(self) -> None:
        self._active = 0
        self._maximum = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            self._maximum = max(self._maximum, self._active)

    def leave(self) -> None:
        with self._lock:
            self._active -= 1

    @property
    def maximum(self) -> int:
        with self._lock:
            return self._maximum


class LangGraphExecutionPlanRuntime:
    """Compile and execute a neutral plan through pinned ``StateGraph`` semantics."""

    def __init__(
        self,
        *,
        node_executor: ExecutionPlanNodeExecutor,
        fan_out: BoundedFanOutScheduler | None = None,
        merge: DeterministicMergeService | None = None,
        conditions: DeclarativeConditionEvaluator | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executor = node_executor
        self._fan_out = fan_out or BoundedFanOutScheduler()
        self._merge = merge or DeterministicMergeService()
        self._conditions = conditions or DeclarativeConditionEvaluator()
        self._clock = clock

    def execute(
        self,
        *,
        plan: ExecutionPlan,
        workflow_input: Mapping[str, Any],
        execution_payload: Mapping[str, Any],
        limits: LangGraphParallelLimits,
        approved_gates: frozenset[str] = frozenset(),
        cancel_requested: Callable[[], bool] | None = None,
        budget: WorkflowBudgetGuard,
        checkpointer: Any = None,
        thread_id: str,
        recursion_limit: int,
    ) -> LangGraphExecutionPlanResult:
        plan.assert_valid()
        limits.assert_valid()
        cancelled = cancel_requested or (lambda: False)
        batches = self._schedule(plan, limits)
        started_at = self._clock()

        unapproved = sorted(
            {node.gate_id for node in plan.nodes if node.gate_id and node.gate_id not in approved_gates}
        )
        if unapproved:
            return self._empty_result(
                plan=plan,
                limits=limits,
                batches=batches,
                status="blocked",
                reason_code=f"approval_required:{','.join(unapproved)}",
                started_at=started_at,
            )
        if cancelled():
            return self._empty_result(
                plan=plan,
                limits=limits,
                batches=batches,
                status="cancelled",
                reason_code="cancel_requested",
                started_at=started_at,
            )
        budget_reason = _declared_budget_violation(plan)
        if budget_reason:
            return self._empty_result(
                plan=plan,
                limits=limits,
                batches=batches,
                status="failed",
                reason_code=budget_reason,
                started_at=started_at,
            )
        if len(batches) + 1 > recursion_limit:
            return self._empty_result(
                plan=plan,
                limits=limits,
                batches=batches,
                status="failed",
                reason_code="langgraph_plan_recursion_limit_exceeded",
                started_at=started_at,
            )

        activity = _ActivityCounter()
        final_state = self._invoke_graph(
            plan=plan,
            batches=batches,
            workflow_input=dict(workflow_input),
            execution_payload=dict(execution_payload),
            approved_gates=approved_gates,
            cancelled=cancelled,
            budget=budget,
            checkpointer=checkpointer,
            thread_id=thread_id,
            recursion_limit=recursion_limit,
            activity=activity,
        )
        records = _records_from_state(final_state)
        status, reason = _terminal_status(plan, records, cancelled=cancelled())
        elapsed = max(0.0, self._clock() - started_at)
        if status in {"completed", "partial"} and elapsed > plan.budget.timeout_seconds:
            status, reason = "failed", "langgraph_plan_budget_exceeded:timeout_seconds"
        actual_budget_reason = _actual_budget_violation(plan, records)
        if status in {"completed", "partial"} and actual_budget_reason:
            status, reason = "failed", actual_budget_reason
        if status in {"completed", "partial"}:
            artifact_reason = _required_artifact_violation(plan, records)
            if artifact_reason:
                status, reason = "failed", artifact_reason
        return LangGraphExecutionPlanResult(
            status=status,
            reason_code=reason,
            plan_hash=plan.plan_hash,
            records=records,
            batches=batches,
            limits=limits,
            max_observed_parallelism=activity.maximum,
            elapsed_seconds=elapsed,
            approved_gates=tuple(sorted(approved_gates)),
        )

    def _schedule(
        self,
        plan: ExecutionPlan,
        limits: LangGraphParallelLimits,
    ) -> tuple[tuple[str, ...], ...]:
        completed: set[str] = set()
        batches: list[tuple[str, ...]] = []
        while len(completed) < len(plan.nodes):
            batch = self._fan_out.select_ready(
                plan,
                completed_node_ids=completed,
                tenant_limit=limits.tenant,
                worker_limit=limits.worker,
                plan_limit=limits.plan,
            )
            node_ids = tuple(candidate.node_id for candidate in batch.candidates)
            if not node_ids:
                raise ValueError("langgraph_parallel_schedule_deadlock")
            batches.append(node_ids)
            completed.update(node_ids)
        return tuple(batches)

    def _invoke_graph(
        self,
        *,
        plan: ExecutionPlan,
        batches: tuple[tuple[str, ...], ...],
        workflow_input: dict[str, Any],
        execution_payload: dict[str, Any],
        approved_gates: frozenset[str],
        cancelled: Callable[[], bool],
        budget: WorkflowBudgetGuard,
        checkpointer: Any,
        thread_id: str,
        recursion_limit: int,
        activity: _ActivityCounter,
    ) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph  # type: ignore[import]

        nodes = {node.node_id: node for node in plan.nodes}
        incoming = _incoming_edges(plan)
        builder: Any = StateGraph(_LangGraphPlanState)
        for node in plan.nodes:
            builder.add_node(
                node.node_id,
                self._node_function(
                    plan=plan,
                    node=node,
                    incoming=incoming[node.node_id],
                    execution_payload=execution_payload,
                    approved_gates=approved_gates,
                    cancelled=cancelled,
                    budget=budget,
                    activity=activity,
                ),
            )

        for index, batch in enumerate(batches):
            if index == 0:
                for node_id in batch:
                    builder.add_edge(START, node_id)
                continue
            previous = list(batches[index - 1])
            for node_id in batch:
                if len(previous) == 1:
                    builder.add_edge(previous[0], node_id)
                else:
                    builder.add_edge(previous, node_id)

        final_node = _reserved_final_node(set(nodes))
        builder.add_node(final_node, lambda _state: {})
        last_batch = list(batches[-1])
        if len(last_batch) == 1:
            builder.add_edge(last_batch[0], final_node)
        else:
            builder.add_edge(last_batch, final_node)
        builder.add_edge(final_node, END)
        compiled = builder.compile(checkpointer=checkpointer)
        return dict(
            compiled.invoke(
                {"workflow_input": workflow_input, "records": []},
                config={
                    "recursion_limit": recursion_limit,
                    "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                },
            )
        )

    def _node_function(
        self,
        *,
        plan: ExecutionPlan,
        node: ExecutionNode,
        incoming: tuple[Any, ...],
        execution_payload: dict[str, Any],
        approved_gates: frozenset[str],
        cancelled: Callable[[], bool],
        budget: WorkflowBudgetGuard,
        activity: _ActivityCounter,
    ) -> Callable[[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        def invoke(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
            records = _record_map(state.get("records") or ())
            if cancelled():
                return _state_update(
                    LangGraphPlanNodeRecord(node.node_id, "cancelled", reason_code="cancel_requested")
                )
            if node.gate_id and node.gate_id not in approved_gates:
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "blocked",
                        reason_code=f"approval_required:{node.gate_id}",
                    )
                )

            route = self._route(node, incoming, records, state.get("workflow_input") or {})
            if route[0] is None:
                return _state_update(
                    LangGraphPlanNodeRecord(node.node_id, "failed", reason_code=route[1])
                )
            if route[0] is False:
                return _state_update(
                    LangGraphPlanNodeRecord(node.node_id, "skipped", reason_code="route_not_selected")
                )
            if node.node_type == "merge":
                return _state_update(self._merge_node(node, incoming, records))
            if node.node_type == "component":
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "failed",
                        reason_code="workflow_component_not_compiled",
                    )
                )

            upstream_failed = tuple(
                sorted(
                    edge.source
                    for edge in incoming
                    if edge.source in records
                    and records[edge.source].status in {"blocked", "cancelled", "failed"}
                )
            )
            if upstream_failed:
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "failed",
                        reason_code=f"upstream_failed:{','.join(upstream_failed)}",
                    )
                )
            try:
                budget.record_step(f"execution_plan_node:{node.node_id}")
                activity.enter()
                try:
                    outcome = self._executor.execute(
                        ExecutionPlanNodeRequest(
                            plan=plan,
                            node=node,
                            workflow_input=dict(state.get("workflow_input") or {}),
                            dependency_results={
                                edge.source: records[edge.source].value
                                for edge in sorted(incoming, key=lambda item: item.source)
                                if edge.source in records and records[edge.source].status == "completed"
                            },
                            execution_payload=dict(execution_payload),
                        ),
                        budget=budget,
                    )
                finally:
                    activity.leave()
                outcome.assert_valid()
                if outcome.status == "failed":
                    return _state_update(
                        LangGraphPlanNodeRecord(
                            node.node_id,
                            "failed",
                            reason_code=outcome.reason_code,
                        )
                    )
                budget_reason = _node_budget_violation(node, plan, outcome)
                if budget_reason:
                    return _state_update(
                        LangGraphPlanNodeRecord(node.node_id, "failed", reason_code=budget_reason)
                    )
                validate_compiled_component_output(node, outcome.value)
                artifacts = _bind_declared_artifacts(node, outcome)
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "completed",
                        value=outcome.value,
                        artifacts=artifacts,
                        tokens=outcome.tokens,
                        cost_micros=outcome.cost_micros,
                    )
                )
            except WorkerError as exc:
                return _state_update(
                    LangGraphPlanNodeRecord(node.node_id, "failed", reason_code=exc.reason_code)
                )
            except ValueError as exc:
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "failed",
                        reason_code=str(exc).split(":", 1)[0] or "langgraph_plan_node_invalid",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - converted into a bounded branch failure
                return _state_update(
                    LangGraphPlanNodeRecord(
                        node.node_id,
                        "failed",
                        reason_code=f"langgraph_plan_node_exception:{type(exc).__name__}",
                    )
                )

        return invoke

    def _route(
        self,
        node: ExecutionNode,
        incoming: tuple[Any, ...],
        records: Mapping[str, LangGraphPlanNodeRecord],
        workflow_input: Mapping[str, Any],
    ) -> tuple[bool | None, str]:
        if not incoming:
            return True, ""
        context = {
            "input": dict(workflow_input),
            "results": {
                node_id: record.value
                for node_id, record in sorted(records.items())
                if record.status == "completed"
            },
            "status": "running",
        }
        evaluations = [self._conditions.evaluate(edge.condition, context) for edge in incoming]
        unknown = next((result for result in evaluations if result.value is None), None)
        if unknown is not None:
            return None, unknown.reason_code
        if node.metadata.get("join_mode") == "all":
            return all(result.matches for result in evaluations), ""
        return any(result.matches for result in evaluations), ""

    def _merge_node(
        self,
        node: ExecutionNode,
        incoming: tuple[Any, ...],
        records: Mapping[str, LangGraphPlanNodeRecord],
    ) -> LangGraphPlanNodeRecord:
        missing = sorted(edge.source for edge in incoming if edge.source not in records)
        if missing:
            return LangGraphPlanNodeRecord(
                node.node_id,
                "failed",
                reason_code=f"merge_branch_missing:{','.join(missing)}",
            )
        merged = self._merge.merge(
            [
                BranchResult(
                    node_id=edge.source,
                    status=(
                        "completed" if records[edge.source].status == "completed" else "failed"
                    ),
                    value=records[edge.source].value,
                    reason_code=records[edge.source].reason_code,
                )
                for edge in incoming
            ],
            strategy=str(node.metadata["merge_strategy"]),
            partial_failure=str(node.metadata.get("partial_failure") or "fail"),
        )
        if merged.status != "completed":
            return LangGraphPlanNodeRecord(
                node.node_id,
                "failed",
                reason_code=merged.reason_code,
                failed_branches=merged.failed_branches,
            )
        artifacts = {artifact_id: merged.value for artifact_id in node.output_artifacts}
        return LangGraphPlanNodeRecord(
            node.node_id,
            "completed",
            value=merged.value,
            artifacts=artifacts,
            reason_code=merged.reason_code,
            failed_branches=merged.failed_branches,
        )

    def _empty_result(
        self,
        *,
        plan: ExecutionPlan,
        limits: LangGraphParallelLimits,
        batches: tuple[tuple[str, ...], ...],
        status: str,
        reason_code: str,
        started_at: float,
    ) -> LangGraphExecutionPlanResult:
        return LangGraphExecutionPlanResult(
            status=status,
            reason_code=reason_code,
            plan_hash=plan.plan_hash,
            records=(),
            batches=batches,
            limits=limits,
            max_observed_parallelism=0,
            elapsed_seconds=max(0.0, self._clock() - started_at),
            approved_gates=(),
        )


def _incoming_edges(plan: ExecutionPlan) -> dict[str, tuple[Any, ...]]:
    values: dict[str, list[Any]] = {node.node_id: [] for node in plan.nodes}
    for edge in plan.edges:
        values[edge.target].append(edge)
    return {
        node_id: tuple(sorted(edges, key=lambda item: (item.source, item.target)))
        for node_id, edges in values.items()
    }


def _records_from_state(state: Mapping[str, Any]) -> tuple[LangGraphPlanNodeRecord, ...]:
    records = _record_map(state.get("records") or ())
    return tuple(records[node_id] for node_id in sorted(records))


def _record_map(values: Any) -> dict[str, LangGraphPlanNodeRecord]:
    records: dict[str, LangGraphPlanNodeRecord] = {}
    for raw in values:
        record = LangGraphPlanNodeRecord.from_mapping(raw)
        if record.node_id in records:
            raise ValueError("langgraph_plan_duplicate_node_record")
        records[record.node_id] = record
    return records


def _state_update(record: LangGraphPlanNodeRecord) -> dict[str, list[dict[str, Any]]]:
    return {"records": [record.to_dict()]}


def _reserved_final_node(existing: set[str]) -> str:
    value = "__ananta_plan_final__"
    while value in existing:
        value += "_"
    return value


def _bind_declared_artifacts(
    node: ExecutionNode,
    outcome: ExecutionPlanNodeOutcome,
) -> dict[str, Any]:
    unexpected = set(outcome.artifacts) - set(node.output_artifacts)
    if unexpected:
        raise ValueError("langgraph_plan_artifact_undeclared")
    if outcome.artifacts:
        missing = set(node.output_artifacts) - set(outcome.artifacts)
        if missing:
            raise ValueError("langgraph_plan_artifact_missing")
        return {key: outcome.artifacts[key] for key in sorted(outcome.artifacts)}
    return {artifact_id: outcome.value for artifact_id in node.output_artifacts}


def _declared_budget_violation(plan: ExecutionPlan) -> str:
    declared_tokens = 0
    declared_cost = 0
    for node in plan.nodes:
        for metadata_key, target in (
            ("estimated_tokens", "tokens"),
            ("estimated_cost_micros", "cost_micros"),
        ):
            raw = node.metadata.get(metadata_key, 0)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                return f"langgraph_plan_declared_budget_invalid:{node.node_id}:{target}"
            if target == "tokens":
                declared_tokens += raw
            else:
                declared_cost += raw
    if plan.budget.max_tokens is not None and declared_tokens > plan.budget.max_tokens:
        return "langgraph_plan_budget_exceeded:declared_tokens"
    if plan.budget.max_cost_micros is not None and declared_cost > plan.budget.max_cost_micros:
        return "langgraph_plan_budget_exceeded:declared_cost_micros"
    return ""


def _node_budget_violation(
    node: ExecutionNode,
    plan: ExecutionPlan,
    outcome: ExecutionPlanNodeOutcome,
) -> str:
    budget = node.budget or plan.budget
    if budget.max_tokens is not None and outcome.tokens > budget.max_tokens:
        return f"langgraph_node_budget_exceeded:{node.node_id}:tokens"
    if budget.max_cost_micros is not None and outcome.cost_micros > budget.max_cost_micros:
        return f"langgraph_node_budget_exceeded:{node.node_id}:cost_micros"
    return ""


def _actual_budget_violation(
    plan: ExecutionPlan,
    records: tuple[LangGraphPlanNodeRecord, ...],
) -> str:
    tokens = sum(record.tokens for record in records)
    cost = sum(record.cost_micros for record in records)
    if plan.budget.max_tokens is not None and tokens > plan.budget.max_tokens:
        return "langgraph_plan_budget_exceeded:tokens"
    if plan.budget.max_cost_micros is not None and cost > plan.budget.max_cost_micros:
        return "langgraph_plan_budget_exceeded:cost_micros"
    return ""


def _required_artifact_violation(
    plan: ExecutionPlan,
    records: tuple[LangGraphPlanNodeRecord, ...],
) -> str:
    required = {artifact.artifact_id for artifact in plan.artifacts if artifact.required}
    actual = {artifact_id for record in records for artifact_id in record.artifacts}
    missing = sorted(required - actual)
    return f"langgraph_required_artifacts_missing:{','.join(missing)}" if missing else ""


def _terminal_status(
    plan: ExecutionPlan,
    records: tuple[LangGraphPlanNodeRecord, ...],
    *,
    cancelled: bool,
) -> tuple[str, str]:
    if cancelled or any(record.status == "cancelled" for record in records):
        return "cancelled", "cancel_requested"
    blocked = next((record for record in records if record.status == "blocked"), None)
    if blocked is not None:
        return "blocked", blocked.reason_code
    node_map = {node.node_id: node for node in plan.nodes}
    tolerated: set[str] = set()
    for record in records:
        if record.status == "completed" and record.failed_branches:
            node = node_map[record.node_id]
            if node.node_type == "merge" and node.metadata.get("partial_failure") == "omit":
                tolerated.update(record.failed_branches)
    failures = [
        record
        for record in records
        if record.status == "failed"
        and not (
            record.node_id in tolerated
            and node_map[record.node_id].metadata.get("failure_policy") == "continue"
        )
    ]
    if failures:
        merge_failure = next(
            (
                record
                for record in sorted(failures, key=lambda item: item.node_id)
                if node_map[record.node_id].node_type == "merge"
            ),
            None,
        )
        first = merge_failure or sorted(failures, key=lambda item: item.node_id)[0]
        return "failed", first.reason_code or "langgraph_plan_node_failed"
    if tolerated:
        return "partial", "merge_partial"
    if len(records) != len(plan.nodes):
        return "failed", "langgraph_plan_incomplete"
    return "completed", ""
