"""Persistent Hub-owned Sprint lifecycle and inspect-and-adapt control."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.services.scrum_state_store import ScrumStateStorePort

_TRANSITIONS = {
    "planned": {"active"},
    "active": {"review"},
    "review": {"retrospective"},
    "retrospective": {"improvement_pending", "closed"},
    "improvement_pending": {"closed"},
    "closed": set(),
    "aborted": set(),
}
_TRIGGERS = {
    "interval",
    "task_completed",
    "task_failed",
    "blocked",
    "handoff_rejected",
    "gate_failed",
    "budget_threshold",
    "scope_change",
    "architecture_constraint_conflict",
    "manual",
}


class ArchitectureHandoffPort(Protocol):
    def handoff(self, *, scope_id: str, sprint_scope: Sequence[str]) -> dict[str, Any]: ...


class ScrumSprintControlService:
    """Own Sprint state while tasks remain owned by the normal Hub queue."""

    def __init__(self, store: ScrumStateStorePort, architecture: ArchitectureHandoffPort) -> None:
        self._store = store
        self._architecture = architecture

    def plan(
        self,
        *,
        sprint_id: str,
        scope_id: str,
        sequence: int,
        predecessor_sprint_id: str | None,
        product_goal: str,
        sprint_goal: str,
        task_ids: Sequence[str],
        sprint_scope: Sequence[str],
        boundary: Mapping[str, int | float],
        planned_at: str,
        improvement_commitment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        if self._store.get("sprint", sprint_id):
            raise ValueError("sprint_already_exists")
        if sequence < 1:
            raise ValueError("sprint_sequence_invalid")
        if sequence == 1 and predecessor_sprint_id:
            raise ValueError("sprint_predecessor_invalid")
        if sequence > 1:
            predecessor = self.require(predecessor_sprint_id or "")
            if predecessor["sequence"] != sequence - 1 or predecessor["scope_id"] != scope_id:
                raise ValueError("sprint_predecessor_invalid")
            if predecessor["lifecycle_state"] not in {"closed", "aborted"}:
                raise ValueError("sprint_predecessor_not_terminal")
        normalized_product = _text(product_goal, "product_goal", maximum=4000)
        normalized_sprint = _text(sprint_goal, "sprint_goal", maximum=4000)
        if normalized_product == normalized_sprint:
            raise ValueError("sprint_goal_must_differ_from_product_goal")
        tasks = _tokens(task_ids, "task_id")
        if not tasks:
            raise ValueError("sprint_task_ids_required")
        normalized_boundary = _boundary(boundary)
        normalized_sprint_scope = _tokens(sprint_scope, "sprint_scope")
        handoff = self._architecture.handoff(scope_id=scope_id, sprint_scope=normalized_sprint_scope)
        commitments = _tokens(improvement_commitment_ids, "commitment_id")
        for commitment_id in commitments:
            commitment = self._store.get("improvement_commitment", commitment_id)
            if commitment is None or commitment["status"] != "accepted" or commitment["scope_id"] != scope_id:
                raise ValueError("sprint_improvement_commitment_invalid")
        payload = {
            "schema": "ananta.scrum-sprint.v1",
            "sprint_id": _text(sprint_id, "sprint_id"),
            "scope_id": _text(scope_id, "scope_id"),
            "sequence": sequence,
            "predecessor_sprint_id": str(predecessor_sprint_id or ""),
            "product_goal": normalized_product,
            "sprint_goal": normalized_sprint,
            "original_task_ids": tasks,
            "task_ids": tasks,
            "unplanned_task_ids": [],
            "scope_changes": [],
            "boundary": normalized_boundary,
            "sprint_scope": normalized_sprint_scope,
            "architecture_handoff": handoff,
            "improvement_commitment_ids": commitments,
            "lifecycle_state": "planned",
            "planned_at": _text(planned_at, "planned_at"),
            "started_at": "",
            "closed_at": "",
            "last_control_sequence": 0,
            "last_control_trigger": "",
            "goal_exception": None,
        }
        return self._store.append("sprint", sprint_id, payload, expected_revision=0)

    def transition(self, *, sprint_id: str, target_state: str, occurred_at: str) -> dict[str, Any]:
        sprint = self.require(sprint_id)
        target = str(target_state or "").strip()
        current = sprint["lifecycle_state"]
        if target == current:
            return sprint
        if target not in _TRANSITIONS.get(current, set()):
            raise ValueError("sprint_lifecycle_transition_invalid")
        if target == "active":
            handoff = self._architecture.handoff(
                scope_id=sprint["scope_id"],
                sprint_scope=sprint["sprint_scope"],
            )
            if (
                handoff["architecture_revision_id"] != sprint["architecture_handoff"]["architecture_revision_id"]
                or handoff["guardrail_digest"] != sprint["architecture_handoff"]["guardrail_digest"]
            ):
                raise ValueError("sprint_architecture_handoff_stale")
        if target in {"review", "retrospective"} and current == "active" and target != "review":
            raise ValueError("sprint_review_must_precede_retrospective")
        updated = {**sprint, "lifecycle_state": target}
        if target == "active":
            updated["started_at"] = _text(occurred_at, "occurred_at")
        if target in {"closed", "aborted"}:
            updated["closed_at"] = _text(occurred_at, "occurred_at")
        return self._store.append("sprint", sprint_id, updated, expected_revision=sprint["revision"])

    def snapshot(
        self,
        *,
        sprint_id: str,
        snapshot_id: str,
        task_states: Mapping[str, str],
        handoff_failures: int,
        gate_failures: int,
        rework_count: int,
        consumed_boundary: Mapping[str, int | float],
        architecture_finding_ids: Sequence[str],
        observed_at: str,
    ) -> dict[str, Any]:
        sprint = self.require(sprint_id)
        if sprint["lifecycle_state"] not in {"active", "review"}:
            raise ValueError("sprint_snapshot_state_invalid")
        if set(task_states) != set(sprint["task_ids"]):
            raise ValueError("sprint_snapshot_task_set_mismatch")
        states = {str(key): str(value) for key, value in task_states.items()}
        if any(value not in {"todo", "active", "done", "failed", "blocked"} for value in states.values()):
            raise ValueError("sprint_snapshot_task_state_invalid")
        consumed = _boundary(consumed_boundary, allow_zero=True)
        ratios = {
            key: consumed[key] / sprint["boundary"][key]
            for key in consumed
            if key in sprint["boundary"] and sprint["boundary"][key] > 0
        }
        completed = sum(value == "done" for value in states.values())
        payload = {
            "schema": "ananta.sprint-progress-snapshot.v1",
            "scope_id": sprint["scope_id"],
            "snapshot_id": _text(snapshot_id, "snapshot_id"),
            "sprint_id": sprint_id,
            "sprint_revision": sprint["revision"],
            "sprint_goal": sprint["sprint_goal"],
            "task_states": states,
            "completion_ratio": completed / len(states),
            "handoff_failures": _count(handoff_failures, "handoff_failures"),
            "gate_failures": _count(gate_failures, "gate_failures"),
            "rework_count": _count(rework_count, "rework_count"),
            "consumed_boundary": consumed,
            "boundary_ratios": ratios,
            "scope_delta": list(sprint["scope_changes"]),
            "architecture_revision_id": sprint["architecture_handoff"]["architecture_revision_id"],
            "architecture_finding_ids": _tokens(architecture_finding_ids, "architecture_finding_id"),
            "observed_at": _text(observed_at, "observed_at"),
        }
        return self._store.append("sprint_snapshot", snapshot_id, payload, expected_revision=0)

    def inspect_and_adapt(
        self,
        *,
        sprint_id: str,
        control_id: str,
        snapshot_id: str,
        trigger: str,
        trigger_sequence: int,
        debounce_sequences: int = 1,
    ) -> dict[str, Any]:
        sprint = self.require(sprint_id)
        snapshot = self._require_snapshot(snapshot_id)
        normalized_trigger = str(trigger or "").strip()
        if normalized_trigger not in _TRIGGERS or snapshot["sprint_id"] != sprint_id:
            raise ValueError("sprint_control_trigger_invalid")
        if trigger_sequence <= sprint["last_control_sequence"]:
            existing = self._store.get("sprint_control", control_id)
            if existing:
                return existing
            raise ValueError("sprint_control_sequence_stale")
        debounced = (
            normalized_trigger == sprint["last_control_trigger"]
            and trigger_sequence - sprint["last_control_sequence"] <= debounce_sequences
        )
        states = snapshot["task_states"].values()
        risks = {
            "failed_tasks": sum(value == "failed" for value in states),
            "blocked_tasks": sum(value == "blocked" for value in states),
            "handoff_failures": snapshot["handoff_failures"],
            "gate_failures": snapshot["gate_failures"],
            "boundary_exceeded": any(value > 1.0 for value in snapshot["boundary_ratios"].values()),
            "architecture_conflicts": len(snapshot["architecture_finding_ids"]),
        }
        risk_points = sum(int(value) for value in risks.values())
        reachability = "on_track" if risk_points == 0 else ("at_risk" if risk_points <= 2 else "unreachable")
        recommendation = "continue"
        if reachability == "at_risk":
            recommendation = "adjust_sprint_backlog"
        elif reachability == "unreachable":
            recommendation = "invoke_goal_exception_policy"
        payload = {
            "schema": "ananta.sprint-control-decision.v1",
            "scope_id": sprint["scope_id"],
            "control_id": _text(control_id, "control_id"),
            "sprint_id": sprint_id,
            "snapshot_id": snapshot_id,
            "trigger": normalized_trigger,
            "trigger_sequence": trigger_sequence,
            "debounced": debounced,
            "reachability": reachability,
            "risk_signals": risks,
            "recommendation": "no_op_debounced" if debounced else recommendation,
            "architecture_revision_id": snapshot["architecture_revision_id"],
        }
        decision = self._store.append("sprint_control", control_id, payload, expected_revision=0)
        if not debounced:
            self._store.append(
                "sprint",
                sprint_id,
                {
                    **sprint,
                    "last_control_sequence": trigger_sequence,
                    "last_control_trigger": normalized_trigger,
                },
                expected_revision=sprint["revision"],
            )
        return decision

    def adjust_backlog(
        self,
        *,
        sprint_id: str,
        control_id: str,
        add_task_ids: Sequence[str],
        remove_task_ids: Sequence[str],
        reason: str,
    ) -> dict[str, Any]:
        sprint = self.require(sprint_id)
        control = self._store.get("sprint_control", control_id)
        if control is None or control["sprint_id"] != sprint_id or control["recommendation"] != "adjust_sprint_backlog":
            raise ValueError("sprint_backlog_adjustment_not_authorized")
        additions = _tokens(add_task_ids, "task_id")
        removals = _tokens(remove_task_ids, "task_id")
        current = list(sprint["task_ids"])
        if set(removals).difference(current) or set(additions).intersection(current):
            raise ValueError("sprint_backlog_adjustment_invalid")
        updated_tasks = [value for value in current if value not in removals] + additions
        if not updated_tasks:
            raise ValueError("sprint_backlog_must_not_be_empty")
        change = {
            "control_id": control_id,
            "added_task_ids": additions,
            "removed_task_ids": removals,
            "reason": _text(reason, "scope_change_reason", maximum=2000),
        }
        return self._store.append(
            "sprint",
            sprint_id,
            {
                **sprint,
                "task_ids": updated_tasks,
                "unplanned_task_ids": sorted(set(sprint["unplanned_task_ids"]).union(additions)),
                "scope_changes": [*sprint["scope_changes"], change],
            },
            expected_revision=sprint["revision"],
        )

    def apply_goal_exception(
        self,
        *,
        sprint_id: str,
        control_id: str,
        action: str,
        replacement_goal: str | None,
        evidence_refs: Sequence[str],
        automated_policy_passed: bool,
        occurred_at: str,
    ) -> dict[str, Any]:
        sprint = self.require(sprint_id)
        control = self._store.get("sprint_control", control_id)
        if (
            control is None
            or control["sprint_id"] != sprint_id
            or control["recommendation"] != "invoke_goal_exception_policy"
            or not automated_policy_passed
            or not evidence_refs
        ):
            raise ValueError("sprint_goal_exception_not_authorized")
        normalized_action = str(action or "").strip()
        if normalized_action not in {"replace_goal", "abort"}:
            raise ValueError("sprint_goal_exception_action_invalid")
        exception = {
            "control_id": control_id,
            "action": normalized_action,
            "previous_goal": sprint["sprint_goal"],
            "replacement_goal": _text(replacement_goal, "replacement_goal", maximum=4000)
            if normalized_action == "replace_goal"
            else "",
            "evidence_refs": _tokens(evidence_refs, "evidence_ref"),
            "automated": True,
        }
        updated = {**sprint, "goal_exception": exception}
        if normalized_action == "replace_goal":
            updated["sprint_goal"] = exception["replacement_goal"]
        else:
            updated["lifecycle_state"] = "aborted"
            updated["closed_at"] = _text(occurred_at, "occurred_at")
        return self._store.append("sprint", sprint_id, updated, expected_revision=sprint["revision"])

    def require(self, sprint_id: str) -> dict[str, Any]:
        sprint = self._store.get("sprint", sprint_id)
        if sprint is None:
            raise ValueError("sprint_unknown")
        return sprint

    def _require_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self._store.get("sprint_snapshot", snapshot_id)
        if snapshot is None:
            raise ValueError("sprint_snapshot_unknown")
        return snapshot


def _boundary(value: Mapping[str, int | float], *, allow_zero: bool = False) -> dict[str, float]:
    allowed = {"time_seconds", "task_count", "token_count", "cost_units"}
    result = {str(key): float(item) for key, item in value.items() if str(key) in allowed}
    minimum = 0.0 if allow_zero else 0.0000001
    if not result or len(result) != len(value) or any(item < minimum for item in result.values()):
        raise ValueError("sprint_boundary_invalid")
    return result


def _count(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"sprint_{field}_invalid")
    return value


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum or "\0" in normalized:
        raise ValueError(f"sprint_{field}_invalid")
    return normalized


def _tokens(values: Sequence[object], field: str) -> list[str]:
    result = [_text(value, field) for value in values]
    if len(result) != len(set(result)) or len(result) > 1000:
        raise ValueError(f"sprint_{field}_invalid")
    return result


def sprint_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["ArchitectureHandoffPort", "ScrumSprintControlService", "sprint_digest"]
