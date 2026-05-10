"""Subworker delegation: envelope, spawn gate, parallel coordinator, artifacts, cancellation.

EW-T038: SubworkerEnvelope — capabilities must be subset of parent.
EW-T039: SubworkerSpawnGate — requires subworker_spawn, blocks cycles, fan-out, recursion.
EW-T040: ParallelExecutionCoordinator — read-only ops parallel, mutations serialized.
EW-T041: DelegationArtifact — audit-reconstructible delegation tree.
EW-T042: CancellationToken + timeout propagation.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from worker.core.execution_envelope import (
    CONFIRM_REQUIRED_CAPABILITIES,
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    WorkerResult,
    WorkerResultStatus,
    make_trace,
)


# ── SubworkerEnvelope (EW-T038) ───────────────────────────────────────────────

class SubworkerEnvelope(BaseModel):
    """Delegated execution contract for a child worker. EW-T038.

    Capability set MUST be a subset of parent capabilities.
    Context set MUST be a subset or policy-approved derivative of parent context.
    """
    parent_execution_id: str
    delegated_task: str
    reduced_capability_grant: CapabilityGrant
    context_subset_refs: list[str] = Field(default_factory=list)
    approval_refs: list[ApprovalRef] = Field(default_factory=list)
    depth: int = 1               # delegation depth, incremented by spawn gate
    max_depth: int = 3           # absolute recursion ceiling
    child_task_id: str = ""
    delegated_by: str = ""

    @field_validator("parent_execution_id", "delegated_task")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    def validate_subset_of(self, parent: ExecutionEnvelope) -> list[str]:
        """Return list of capability violations (child must not exceed parent)."""
        errors = []
        parent_caps = set(parent.capability_grant.capabilities)
        child_caps = set(self.reduced_capability_grant.capabilities)
        excess = child_caps - parent_caps
        if excess:
            errors.append(f"child capabilities exceed parent: {sorted(excess)!r}")
        return errors


# ── SpawnGateResult ───────────────────────────────────────────────────────────

@dataclass
class SpawnGateResult:
    allowed: bool
    reason_code: str
    detail: str = ""


# ── SubworkerSpawnGate (EW-T039) ──────────────────────────────────────────────

class SubworkerSpawnGate:
    """Validates subworker spawn requests. EW-T039.

    Blocks:
    - Missing subworker_spawn capability
    - Missing reduced_capability_grant
    - Capability set exceeding parent
    - Depth > max_depth (recursion ceiling)
    - Simultaneous fan-out exceeding max_fan_out
    """

    MAX_FAN_OUT = 8

    def check(
        self,
        parent: ExecutionEnvelope,
        sub_env: SubworkerEnvelope,
        *,
        current_fan_out: int = 0,
    ) -> SpawnGateResult:
        # 1. Parent must have subworker_spawn capability
        if not parent.has_capability("subworker_spawn"):
            return SpawnGateResult(False, "missing_capability",
                                   "parent lacks subworker_spawn capability")

        # 2. Parent must have approval if confirm_required
        if "subworker_spawn" in CONFIRM_REQUIRED_CAPABILITIES:
            if not parent.approval_for("subworker_spawn"):
                return SpawnGateResult(False, "approval_missing",
                                       "subworker_spawn requires ApprovalRef")

        # 3. Reduced capability grant must exist and be non-empty
        if not sub_env.reduced_capability_grant.capabilities:
            return SpawnGateResult(False, "missing_capability",
                                   "SubworkerEnvelope has no capabilities")

        # 4. Capability subset check
        errors = sub_env.validate_subset_of(parent)
        if errors:
            return SpawnGateResult(False, "missing_capability", "; ".join(errors))

        # 5. Depth / recursion ceiling
        if sub_env.depth > sub_env.max_depth:
            return SpawnGateResult(False, "subworker_depth_exceeded",
                                   f"depth {sub_env.depth} > max {sub_env.max_depth}")

        # 6. Fan-out ceiling
        if current_fan_out >= self.MAX_FAN_OUT:
            return SpawnGateResult(False, "subworker_fanout_exceeded",
                                   f"fan-out {current_fan_out} >= max {self.MAX_FAN_OUT}")

        return SpawnGateResult(True, "spawn_allow")

    def make_child_envelope(
        self,
        parent: ExecutionEnvelope,
        sub_env: SubworkerEnvelope,
    ) -> ExecutionEnvelope:
        """Create a proper ExecutionEnvelope for the child from SubworkerEnvelope."""
        return ExecutionEnvelope(
            task_id=sub_env.child_task_id or f"{parent.task_id}:child",
            goal_id=parent.goal_id,
            actor_ref=parent.actor_ref,
            capability_grant=sub_env.reduced_capability_grant,
            context_envelope_ref=parent.context_envelope_ref,
            model_policy=parent.model_policy,
            tool_policy=parent.tool_policy,
            approval_refs=sub_env.approval_refs,
            filesystem_scope=parent.filesystem_scope,
            network_scope=parent.network_scope,
            audit_correlation_id=f"{parent.audit_correlation_id}:child",
            trace_parent_id=parent.audit_correlation_id,
        )


# ── DelegationArtifact (EW-T041) ─────────────────────────────────────────────

@dataclass
class DelegationRecord:
    child_task_id: str
    objective: str
    capabilities: list[str]
    context_refs: list[str]
    result_status: str
    artifact_ids: list[str] = field(default_factory=list)
    depth: int = 1


@dataclass
class DelegationArtifact:
    """Audit-reconstructible delegation tree entry. EW-T041."""
    artifact_id: str
    parent_task_id: str
    records: list[DelegationRecord] = field(default_factory=list)

    def add_record(self, record: DelegationRecord) -> None:
        self.records.append(record)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "delegation_artifact",
            "artifact_id": self.artifact_id,
            "parent_task_id": self.parent_task_id,
            "child_count": len(self.records),
            "records": [
                {
                    "child_task_id": r.child_task_id,
                    "objective": r.objective[:200],
                    "capabilities": r.capabilities,
                    "context_refs": r.context_refs,
                    "result_status": r.result_status,
                    "artifact_ids": r.artifact_ids,
                    "depth": r.depth,
                }
                for r in self.records
            ],
        }


# ── CancellationToken (EW-T042) ───────────────────────────────────────────────

class CancellationToken:
    """Cooperative cancellation propagated to children. EW-T042."""

    def __init__(self, reason: str = "cancelled") -> None:
        self._cancelled = threading.Event()
        self.reason = reason
        self.cancelled_at: float | None = None
        self._children: list[CancellationToken] = []

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        if not self._cancelled.is_set():
            self.cancelled_at = time.time()
            self._cancelled.set()
            for child in self._children:
                child.cancel()

    def spawn_child(self) -> "CancellationToken":
        child = CancellationToken(self.reason)
        self._children.append(child)
        return child

    def wait(self, timeout: float | None = None) -> bool:
        return self._cancelled.wait(timeout=timeout)


# ── ExecutionState vocabulary ─────────────────────────────────────────────────

class ExecutionState(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


@dataclass
class CoordinatedTask:
    task_id: str
    is_mutation: bool           # True → must be serialized
    is_independent: bool = False
    state: ExecutionState = ExecutionState.pending
    result: Any = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def duration(self) -> float | None:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None


# ── ParallelExecutionCoordinator (EW-T040) ───────────────────────────────────

class ParallelExecutionCoordinator:
    """Manages parallel vs serialized task execution. EW-T040.

    Rules:
    - Read-only, independent, isolated tasks → parallel.
    - patch/apply/write/shell mutation tasks → serialized.
    - Deterministic result ordering preserved.
    """

    MUTATION_CAPABILITIES = frozenset({
        "patch_apply", "shell_execute", "memory_write", "cron_schedule",
    })

    def classify(self, task: CoordinatedTask, envelope: ExecutionEnvelope) -> CoordinatedTask:
        """Classify a task as mutation or parallel-safe."""
        caps = set(envelope.capability_grant.capabilities)
        task.is_mutation = bool(caps & self.MUTATION_CAPABILITIES)
        return task

    def plan(
        self,
        tasks: list[CoordinatedTask],
        envelope: ExecutionEnvelope,
    ) -> tuple[list[CoordinatedTask], list[CoordinatedTask]]:
        """Split tasks into (parallel_safe, serialized).

        Serialized tasks preserve input order for determinism.
        """
        classified = [self.classify(t, envelope) for t in tasks]
        parallel_safe = [t for t in classified if not t.is_mutation or t.is_independent]
        serialized = [t for t in classified if t.is_mutation and not t.is_independent]
        return parallel_safe, serialized

    def merge_results(
        self,
        parallel_results: list[CoordinatedTask],
        serial_results: list[CoordinatedTask],
        original_order: list[str],
    ) -> list[CoordinatedTask]:
        """Merge results in deterministic original order. EW-T040."""
        by_id = {t.task_id: t for t in parallel_results + serial_results}
        return [by_id[tid] for tid in original_order if tid in by_id]
