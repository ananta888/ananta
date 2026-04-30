from __future__ import annotations

from typing import Any


def decide_workflow_policy(descriptor: Any, *, approved: bool = False, safe_read_auto_allow: bool = True) -> dict[str, Any]:
    capability = str(getattr(descriptor, "capability", "read")).lower()
    risk_class = str(getattr(descriptor, "risk_class", "medium")).lower()
    approval_required = bool(getattr(descriptor, "approval_required", capability in {"write", "admin"}))

    if capability in {"write", "admin"} or risk_class in {"high", "critical"} or approval_required:
        if approved:
            return {"decision": "allow", "requires_approval": True, "reason": "approved"}
        return {"decision": "confirm_required", "requires_approval": True, "reason": "approval_missing"}

    if capability == "read" and safe_read_auto_allow:
        return {"decision": "allow", "requires_approval": False, "reason": "safe_read"}

    return {"decision": "blocked", "requires_approval": False, "reason": "policy_blocked"}
