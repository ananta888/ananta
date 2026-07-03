"""AgentRunStateMachine — COSMOS-002

Manages the lifecycle of individual agent runs. State transitions are
explicit, validated, and fully auditable via state_history.

Design doc: docs/architecture/agent-runtime-state-machine.md
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRunState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    WAITING_FOR_CONTEXT = "waiting_for_context"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({
    AgentRunState.COMPLETED,
    AgentRunState.FAILED,
    AgentRunState.CANCELLED,
})

ALLOWED_TRANSITIONS: dict[AgentRunState, frozenset[AgentRunState]] = {
    AgentRunState.CREATED: frozenset({
        AgentRunState.QUEUED,
        AgentRunState.CANCELLED,
    }),
    AgentRunState.QUEUED: frozenset({
        AgentRunState.PLANNING,
        AgentRunState.CANCELLED,
        AgentRunState.FAILED,
    }),
    AgentRunState.PLANNING: frozenset({
        AgentRunState.WAITING_FOR_CONTEXT,
        AgentRunState.WAITING_FOR_APPROVAL,
        AgentRunState.RUNNING,
        AgentRunState.FAILED,
        AgentRunState.CANCELLED,
    }),
    AgentRunState.WAITING_FOR_CONTEXT: frozenset({
        AgentRunState.PLANNING,
        AgentRunState.RUNNING,
        AgentRunState.FAILED,
        AgentRunState.CANCELLED,
    }),
    AgentRunState.WAITING_FOR_APPROVAL: frozenset({
        AgentRunState.RUNNING,
        AgentRunState.CANCELLED,
        AgentRunState.FAILED,
    }),
    AgentRunState.RUNNING: frozenset({
        AgentRunState.VERIFYING,
        AgentRunState.WAITING_FOR_APPROVAL,
        AgentRunState.FAILED,
        AgentRunState.CANCELLED,
    }),
    AgentRunState.VERIFYING: frozenset({
        AgentRunState.COMPLETED,
        AgentRunState.FAILED,
        AgentRunState.RUNNING,
    }),
    AgentRunState.COMPLETED: frozenset(),
    AgentRunState.FAILED: frozenset({
        AgentRunState.QUEUED,
    }),
    AgentRunState.CANCELLED: frozenset(),
}


class AgentRunStateError(ValueError):
    """Raised when an invalid state transition is attempted."""


@dataclass
class AgentRunRecord:
    run_id: str
    goal_id: str | None
    correlation_id: str
    expert_id: str | None
    policy_scope_id: str | None
    state: AgentRunState
    created_at: float
    updated_at: float
    failed_at_step: str | None
    error_code: str | None
    error_reason: str | None
    artifacts: list[str]
    recovery_options: list[str]
    state_history: list[dict]  # [{state, timestamp, reason}]

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def is_active(self) -> bool:
        return not self.is_terminal()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "correlation_id": self.correlation_id,
            "expert_id": self.expert_id,
            "policy_scope_id": self.policy_scope_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "failed_at_step": self.failed_at_step,
            "error_code": self.error_code,
            "error_reason": self.error_reason,
            "artifacts": list(self.artifacts),
            "recovery_options": list(self.recovery_options),
            "state_history": list(self.state_history),
        }


class AgentRunStateMachine:
    """Manages state transitions for agent runs. Dict-based in-memory registry."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}

    def create(
        self,
        *,
        goal_id: str | None = None,
        expert_id: str | None = None,
        policy_scope_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentRunRecord:
        run_id = str(uuid.uuid4())
        now = time.time()
        record = AgentRunRecord(
            run_id=run_id,
            goal_id=goal_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            expert_id=expert_id,
            policy_scope_id=policy_scope_id,
            state=AgentRunState.CREATED,
            created_at=now,
            updated_at=now,
            failed_at_step=None,
            error_code=None,
            error_reason=None,
            artifacts=[],
            recovery_options=[],
            state_history=[
                {"state": AgentRunState.CREATED.value, "timestamp": now, "reason": "created"}
            ],
        )
        self._runs[run_id] = record
        return record

    def _get_or_raise(self, run_id: str) -> AgentRunRecord:
        r = self._runs.get(run_id)
        if r is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return r

    def _validate_transition(
        self, record: AgentRunRecord, to_state: AgentRunState
    ) -> None:
        allowed = ALLOWED_TRANSITIONS.get(record.state, frozenset())
        if to_state not in allowed:
            raise AgentRunStateError(
                f"Cannot transition {record.state.value} → {to_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

    def _apply_transition(
        self, record: AgentRunRecord, to_state: AgentRunState, reason: str = ""
    ) -> AgentRunRecord:
        self._validate_transition(record, to_state)
        now = time.time()
        record.state = to_state
        record.updated_at = now
        record.state_history.append(
            {"state": to_state.value, "timestamp": now, "reason": reason}
        )
        return record

    def transition(
        self, run_id: str, to_state: AgentRunState, *, reason: str = ""
    ) -> AgentRunRecord:
        return self._apply_transition(self._get_or_raise(run_id), to_state, reason)

    def cancel(self, run_id: str, *, reason: str = "") -> AgentRunRecord:
        record = self._get_or_raise(run_id)
        if record.is_terminal():
            raise AgentRunStateError(
                f"Cannot cancel terminal run (state={record.state.value})"
            )
        return self._apply_transition(record, AgentRunState.CANCELLED, reason or "cancelled")

    def fail(
        self,
        run_id: str,
        *,
        failed_at_step: str = "",
        error_code: str = "unknown_error",
        error_reason: str = "",
        recovery_options: list[str] | None = None,
    ) -> AgentRunRecord:
        record = self._get_or_raise(run_id)
        self._validate_transition(record, AgentRunState.FAILED)
        record.failed_at_step = failed_at_step or None
        record.error_code = error_code
        record.error_reason = error_reason or None
        record.recovery_options = list(recovery_options or ["retry", "abort"])
        return self._apply_transition(record, AgentRunState.FAILED, f"failed: {error_code}")

    def complete(
        self, run_id: str, *, artifacts: list[str] | None = None
    ) -> AgentRunRecord:
        record = self._get_or_raise(run_id)
        self._validate_transition(record, AgentRunState.COMPLETED)
        if artifacts:
            record.artifacts = list(artifacts)
        return self._apply_transition(record, AgentRunState.COMPLETED, "completed")

    def retry(self, run_id: str) -> AgentRunRecord:
        record = self._get_or_raise(run_id)
        if record.state != AgentRunState.FAILED:
            raise AgentRunStateError(
                f"retry() only allowed from FAILED state, got {record.state.value}"
            )
        record.failed_at_step = None
        record.error_code = None
        record.error_reason = None
        return self._apply_transition(record, AgentRunState.QUEUED, "retry")

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self._runs.get(run_id)

    def get_by_state(self, state: AgentRunState) -> list[AgentRunRecord]:
        return [r for r in self._runs.values() if r.state == state]

    def can_transition(self, run_id: str, to_state: AgentRunState) -> bool:
        record = self._runs.get(run_id)
        if record is None:
            return False
        return to_state in ALLOWED_TRANSITIONS.get(record.state, frozenset())
