from __future__ import annotations

from typing import Any


def should_stop_loop(state: dict[str, Any]) -> tuple[bool, str]:
    if str(state.get("policy_state") or "") == "deny":
        return (True, "policy_denied")
    if str(state.get("policy_state") or "") in {"approval_required", "default_deny"} and not bool(state.get("approval")):
        return (True, "approval_required")
    if bool(state.get("repeated_failure")):
        return (True, "repeated_failure")
    if bool(state.get("no_progress_detected")):
        return (True, "no_progress_detected")
    if bool(state.get("budget_exhausted")):
        return (True, "budget_exhausted")
    return (False, "")
