from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FlowTransition:
    allowed: bool
    next_state: str
    outcome: str
    reason_code: str

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


class AegisFlow:
    _TRANSITIONS: dict[tuple[str, str], tuple[str, str, str]] = {
        ("goal", "plan_created"): ("plan", "progress", "goal_to_plan"),
        ("plan", "task_ready"): ("task", "progress", "plan_to_task"),
        ("task", "action_started"): ("action", "progress", "task_to_action"),
        ("action", "action_completed"): ("verification", "progress", "action_to_verification"),
        ("verification", "verification_passed"): ("artifact", "progress", "verification_to_artifact"),
        ("artifact", "artifact_recorded"): ("completed", "approved", "artifact_to_completed"),
        ("verification", "verification_failed"): ("retry", "retry", "verification_failed_retry"),
        ("retry", "retry_action"): ("action", "retry", "retry_to_action"),
        ("verification", "policy_violation"): ("rollback", "rollback", "policy_violation_rollback"),
    }

    def advance(self, *, state: str, event: str) -> FlowTransition:
        key = (str(state or "").strip().lower(), str(event or "").strip().lower())
        target = self._TRANSITIONS.get(key)
        if target is None:
            return FlowTransition(
                allowed=False,
                next_state=key[0] or "unknown",
                outcome="blocked",
                reason_code="invalid_transition",
            )
        next_state, outcome, reason_code = target
        return FlowTransition(allowed=True, next_state=next_state, outcome=outcome, reason_code=reason_code)
