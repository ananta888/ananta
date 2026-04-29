from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEGRADED_STATES = {
    "unavailable_model": "Model provider is unavailable or not configured.",
    "unavailable_external_tool": "External coding tool adapter is unavailable.",
    "missing_git_repo": "Working directory is not a valid git repository.",
    "denied_policy": "Policy denied execution for this action.",
    "missing_approval": "Approval is required before execution can continue.",
    "schema_invalid": "Worker artifact schema validation failed.",
    "budget_exhausted": "Execution stopped because runtime budgets were exhausted.",
    "unsafe_command": "Command or instruction was blocked as unsafe.",
    "prompt_injection_blocked": "Artifact-sourced instructions were blocked by prompt-injection guardrails.",
}

_MACHINE_REASON_ALIASES = {
    "policy_denied": "policy_denied",
    "denied_policy": "policy_denied",
    "approval_required": "hub_approval_token_missing",
    "missing_approval": "hub_approval_token_missing",
    "schema_invalid": "schema_invalid",
    "schema_validation_failed": "schema_invalid",
    "budget_exhausted": "budget_exhausted",
    "unsafe_command": "unsafe_command",
    "prompt_injection_blocked": "prompt_injection_blocked",
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
    normalized_reason = normalize_machine_reason(str(machine_reason or "").strip() or normalized_state)
    degraded = DegradedState(
        state=normalized_state,
        readable_message=DEGRADED_STATES[normalized_state],
        machine_reason=normalized_reason,
        details=dict(details or {}),
    )
    return degraded.as_dict()


def normalize_machine_reason(machine_reason: str) -> str:
    normalized = str(machine_reason or "").strip().lower()
    if not normalized:
        return "unavailable_external_tool"
    return _MACHINE_REASON_ALIASES.get(normalized, normalized)
