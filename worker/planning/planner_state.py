from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_STATES = ("draft", "ready", "executing", "verifying", "replanning", "complete", "failed")
_ALLOWED_TRANSITIONS = {
    "draft": {"ready", "failed"},
    "ready": {"executing", "failed"},
    "executing": {"verifying", "replanning", "failed"},
    "verifying": {"complete", "replanning", "failed"},
    "replanning": {"ready", "failed"},
    "complete": set(),
    "failed": set(),
}


@dataclass(frozen=True)
class PlannerState:
    state: str
    trace_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "trace_ref": self.trace_ref}


def normalize_state(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_STATES:
        return normalized
    raise ValueError(f"planner_state_invalid:{normalized or '<missing>'}")


def transition_state(*, current_state: str, next_state: str, trace_ref: str) -> PlannerState:
    current = normalize_state(current_state)
    target = normalize_state(next_state)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"planner_state_transition_invalid:{current}->{target}")
    return PlannerState(state=target, trace_ref=str(trace_ref or "").strip() or "trace:missing")

