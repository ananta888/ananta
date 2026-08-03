"""Bounded organization workflow feedback/rework decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class OrganizationLoopPolicy:
    loop_id: str
    source_phase: str
    target_phase: str
    max_iterations: int
    timeout_seconds: int
    exit_condition: str
    on_exhausted_policy: str


@dataclass(frozen=True, slots=True)
class OrganizationLoopState:
    loop_id: str
    iteration: int
    status: str
    started_at: str
    updated_at: str
    accumulated_cost: str
    artifact_versions: tuple[str, ...]
    selected_transition: str | None = None


@dataclass(frozen=True, slots=True)
class OrganizationLoopDecision:
    state: OrganizationLoopState
    reason_code: str
    creates_dependency_edge: bool = False


class OrganizationWorkflowLoopStorePort(Protocol):
    def get(self, loop_instance_id: str) -> dict[str, Any] | None: ...

    def create_once(
        self,
        value: Mapping[str, Any],
    ) -> tuple[bool, dict[str, Any]]: ...

    def save_if_revision(
        self,
        *,
        loop_instance_id: str,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> bool: ...


class OrganizationWorkflowLoopService:
    """Models feedback as bounded transitions, never as cyclic depends_on."""

    _EXHAUSTED_POLICIES = frozenset({"block", "human_escalation"})

    def validate_policy(
        self,
        policy: OrganizationLoopPolicy,
        *,
        phase_capabilities: Mapping[str, frozenset[str]],
    ) -> tuple[str, ...]:
        issues: list[str] = []
        if not policy.loop_id or not policy.source_phase or not policy.target_phase:
            issues.append("loop_binding_missing")
        if policy.source_phase == policy.target_phase:
            issues.append("loop_source_target_equal")
        if policy.source_phase not in phase_capabilities or policy.target_phase not in phase_capabilities:
            issues.append("loop_phase_unknown")
        if policy.max_iterations <= 0 or policy.max_iterations > 20:
            issues.append("loop_max_iterations_invalid")
        if policy.timeout_seconds <= 0 or policy.timeout_seconds > 31_536_000:
            issues.append("loop_timeout_invalid")
        if not policy.exit_condition:
            issues.append("loop_exit_condition_missing")
        if policy.on_exhausted_policy not in self._EXHAUSTED_POLICIES:
            issues.append("loop_exhausted_policy_invalid")
        return tuple(sorted(set(issues)))

    def request_rework(
        self,
        *,
        policy: OrganizationLoopPolicy,
        state: OrganizationLoopState,
        artifact_version: str,
        incremental_cost: str,
        exit_condition_satisfied: bool,
        timed_out: bool = False,
        now: datetime | None = None,
    ) -> OrganizationLoopDecision:
        timestamp = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
        if state.loop_id != policy.loop_id:
            return OrganizationLoopDecision(
                replace(state, status="blocked", updated_at=timestamp), "loop_state_policy_mismatch"
            )
        if state.status in {"completed", "blocked", "escalated", "cancelled"}:
            return OrganizationLoopDecision(state, "loop_already_terminal")
        if not str(artifact_version or "").strip():
            return OrganizationLoopDecision(
                replace(state, status="blocked", updated_at=timestamp),
                "loop_artifact_version_missing",
            )
        try:
            current_cost = Decimal(state.accumulated_cost or "0")
            added_cost = Decimal(incremental_cost or "0")
            if not current_cost.is_finite() or not added_cost.is_finite() or added_cost < 0:
                raise InvalidOperation
            total_cost = str(current_cost + added_cost)
        except (InvalidOperation, ValueError):
            return OrganizationLoopDecision(
                replace(state, status="blocked", updated_at=timestamp),
                "loop_cost_invalid",
            )
        audited_state = replace(
            state,
            updated_at=timestamp,
            accumulated_cost=total_cost,
            artifact_versions=(*state.artifact_versions, artifact_version),
        )
        if exit_condition_satisfied:
            return OrganizationLoopDecision(
                replace(audited_state, status="completed", selected_transition="exit"),
                "loop_exit_condition_satisfied",
            )
        exhausted = timed_out or state.iteration >= policy.max_iterations
        if exhausted:
            status = "escalated" if policy.on_exhausted_policy == "human_escalation" else "blocked"
            transition = "human_gate" if status == "escalated" else "blocked"
            return OrganizationLoopDecision(
                replace(audited_state, status=status, selected_transition=transition),
                "loop_timeout_exhausted" if timed_out else "loop_iteration_exhausted",
            )
        next_state = replace(
            audited_state,
            iteration=state.iteration + 1,
            status="rework_requested",
            selected_transition=f"{policy.source_phase}->{policy.target_phase}",
        )
        # Feedback is an event/transition; the authoritative task DAG remains acyclic.
        return OrganizationLoopDecision(next_state, "loop_rework_transition_selected", creates_dependency_edge=False)


__all__ = [
    "OrganizationLoopDecision",
    "OrganizationLoopPolicy",
    "OrganizationLoopState",
    "OrganizationWorkflowLoopService",
    "OrganizationWorkflowLoopStorePort",
]
