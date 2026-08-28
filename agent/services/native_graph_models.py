"""Runtime-neutral contracts and state for the Hub-owned Native graph."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.security import SignedCheckpoint, WorkflowState

NATIVE_GRAPH_RUNTIME_ID = "ananta-native"
NATIVE_GRAPH_RUNTIME_VERSION = "1.0.0"
NATIVE_GRAPH_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class NativeControlPolicyPort(Protocol):
    """Hub policy seam. Missing or negative decisions fail closed."""

    def authorize_command(
        self,
        command: SignedWorkflowCommand,
        *,
        plan: ExecutionPlan,
        state: "NativeRunState",
    ) -> tuple[bool, str]: ...

    def authorize_delegation(
        self,
        *,
        plan: ExecutionPlan,
        node: ExecutionNode,
        state: "NativeRunState",
    ) -> tuple[bool, str]: ...


class WorkflowPlanArtifactPort(Protocol):
    def load_plan(self, *, tenant_id: str, plan_ref: str) -> ExecutionPlan: ...


@dataclass(frozen=True)
class NativeGraphRequest:
    plan: ExecutionPlan
    run_id: str
    control_task_id: str
    correlation_id: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    secret_refs: tuple[str, ...] = ()
    tenant_parallel_limit: int = 4
    worker_parallel_limit: int = 4

    def assert_valid(self) -> None:
        self.plan.assert_valid()
        if not self.run_id or not self.control_task_id:
            raise ValueError("native_graph_request_binding_required")
        if self.tenant_parallel_limit < 1 or self.worker_parallel_limit < 1:
            raise ValueError("native_graph_parallel_limit_invalid")
        if any("://" not in value for value in self.secret_refs):
            raise ValueError("native_graph_secret_reference_invalid")


@dataclass(frozen=True)
class NativeGraphValidation:
    valid: bool
    reason_codes: tuple[str, ...] = ()
    plan_hash: str = ""


@dataclass(frozen=True)
class NativeGraphResult:
    runtime_id: str
    runtime_version: str
    tenant_id: str
    workflow_id: str
    run_id: str
    control_task_id: str
    status: str
    checkpoint: SignedCheckpoint
    event_cursor: int
    completed_node_ids: tuple[str, ...]
    failed_nodes: dict[str, str]
    open_gates: tuple[str, ...]
    artifact_refs: dict[str, str]
    reason_code: str = ""
    effective_plan: ExecutionPlan | None = None


@dataclass
class NativeRunState:
    status: str = "running"
    input_data: dict[str, Any] = field(default_factory=dict)
    node_results: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, str] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    running: dict[str, dict[str, Any]] = field(default_factory=dict)
    approved_gates: set[str] = field(default_factory=set)
    open_gates: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    budget_usage: dict[str, int | float] = field(default_factory=dict)
    event_sequence: int = 0
    plan_revision: int = 1
    tenant_parallel_limit: int = 1
    worker_parallel_limit: int = 1
    reason_code: str = ""
    base_plan_hash: str = ""
    effective_plan: dict[str, Any] = field(default_factory=dict)
    last_command_id: str = ""
    last_command_fingerprint: str = ""

    @classmethod
    def from_workflow_state(cls, state: WorkflowState) -> "NativeRunState":
        business = dict(state.business_data)
        runtime = dict(state.runtime_metadata)
        return cls(
            status=str(runtime.get("status") or "running"),
            input_data=dict(business.get("input_data") or {}),
            node_results=dict(business.get("node_results") or {}),
            artifact_refs={str(key): str(value) for key, value in dict(runtime.get("artifact_refs") or {}).items()},
            completed=set(runtime.get("completed") or ()),
            skipped=set(runtime.get("skipped") or ()),
            failed={str(key): str(value) for key, value in dict(runtime.get("failed") or {}).items()},
            running={str(key): dict(value) for key, value in dict(runtime.get("running") or {}).items()},
            approved_gates=set(runtime.get("approved_gates") or ()),
            open_gates={str(key): str(value) for key, value in dict(runtime.get("open_gates") or {}).items()},
            attempts={str(key): int(value) for key, value in dict(runtime.get("attempts") or {}).items()},
            budget_usage={str(key): value for key, value in dict(runtime.get("budget_usage") or {}).items()},
            event_sequence=int(runtime.get("event_sequence") or 0),
            plan_revision=int(runtime.get("plan_revision") or 1),
            tenant_parallel_limit=int(runtime.get("tenant_parallel_limit") or 1),
            worker_parallel_limit=int(runtime.get("worker_parallel_limit") or 1),
            reason_code=str(runtime.get("reason_code") or ""),
            base_plan_hash=str(runtime.get("base_plan_hash") or ""),
            effective_plan=dict(business.get("effective_plan") or {}),
            last_command_id=str(runtime.get("last_command_id") or ""),
            last_command_fingerprint=str(runtime.get("last_command_fingerprint") or ""),
        )

    def to_workflow_state(self, *, secret_refs: tuple[str, ...]) -> WorkflowState:
        value = WorkflowState(
            business_data={
                "input_data": dict(self.input_data),
                "node_results": dict(self.node_results),
                "effective_plan": dict(self.effective_plan),
            },
            runtime_metadata={
                "status": self.status,
                "artifact_refs": dict(sorted(self.artifact_refs.items())),
                "completed": sorted(self.completed),
                "skipped": sorted(self.skipped),
                "failed": dict(sorted(self.failed.items())),
                "running": {key: dict(self.running[key]) for key in sorted(self.running)},
                "approved_gates": sorted(self.approved_gates),
                "open_gates": dict(sorted(self.open_gates.items())),
                "attempts": dict(sorted(self.attempts.items())),
                "budget_usage": dict(sorted(self.budget_usage.items())),
                "event_sequence": self.event_sequence,
                "plan_revision": self.plan_revision,
                "tenant_parallel_limit": self.tenant_parallel_limit,
                "worker_parallel_limit": self.worker_parallel_limit,
                "reason_code": self.reason_code,
                "base_plan_hash": self.base_plan_hash,
                "last_command_id": self.last_command_id,
                "last_command_fingerprint": self.last_command_fingerprint,
            },
            secret_refs=secret_refs,
            artifact_refs=tuple(sorted(set(self.artifact_refs.values()))),
            open_gates=tuple(sorted(self.open_gates)),
        )
        value.assert_safe()
        return value


def native_budget_mapping(budget: Any) -> dict[str, int | float]:
    values: dict[str, int | float] = {
        "attempts": budget.max_attempts,
        "timeout_seconds": budget.timeout_seconds,
    }
    if budget.max_tokens is not None:
        values["tokens"] = budget.max_tokens
    if budget.max_cost_micros is not None:
        values["cost_micros"] = budget.max_cost_micros
    return values


def safe_native_reason_code(value: str) -> str:
    reason = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,239}", reason):
        return reason
    return "native_graph_failed"


__all__ = [
    "NATIVE_GRAPH_RUNTIME_ID",
    "NATIVE_GRAPH_RUNTIME_VERSION",
    "NATIVE_GRAPH_TERMINAL_STATUSES",
    "NativeControlPolicyPort",
    "NativeGraphRequest",
    "NativeGraphResult",
    "NativeGraphValidation",
    "NativeRunState",
    "WorkflowPlanArtifactPort",
    "native_budget_mapping",
    "safe_native_reason_code",
]
