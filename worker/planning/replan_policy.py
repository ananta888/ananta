from __future__ import annotations

from typing import Any

_TRIGGERS = {
    "verification_failure",
    "missing_artifact",
    "policy_denied",
    "budget_exhausted",
}


def should_replan(
    *,
    trigger: str,
    attempts_used: int,
    max_attempts: int,
    profile: str,
) -> dict[str, Any]:
    normalized_trigger = str(trigger or "").strip().lower()
    normalized_profile = str(profile or "balanced").strip().lower() or "balanced"
    if normalized_trigger not in _TRIGGERS:
        return {"replan": False, "reason": "unknown_trigger", "profile": normalized_profile}
    if int(attempts_used) >= int(max_attempts):
        return {"replan": False, "reason": "replan_budget_exhausted", "profile": normalized_profile}
    return {"replan": True, "reason": normalized_trigger, "profile": normalized_profile}

