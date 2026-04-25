from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEGRADED_STATES = {
    "unavailable_model": "Model provider is unavailable or not configured.",
    "unavailable_external_tool": "External coding tool adapter is unavailable.",
    "missing_git_repo": "Working directory is not a valid git repository.",
    "denied_policy": "Policy denied execution for this action.",
    "missing_approval": "Approval is required before execution can continue.",
}


@dataclass(frozen=True)
class DegradedState:
    state: str
    readable_message: str
    machine_reason: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "worker_degraded_state.v1",
            "state": self.state,
            "readable_message": self.readable_message,
            "machine_reason": self.machine_reason,
            "status": "degraded",
            "details": dict(self.details),
        }


def build_degraded_state(
    *,
    state: str,
    machine_reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_state = str(state).strip()
    if normalized_state not in DEGRADED_STATES:
        normalized_state = "unavailable_external_tool"
    degraded = DegradedState(
        state=normalized_state,
        readable_message=DEGRADED_STATES[normalized_state],
        machine_reason=str(machine_reason or "").strip() or normalized_state,
        details=dict(details or {}),
    )
    return degraded.as_dict()
