from __future__ import annotations


def render_policy_state(code: str) -> str:
    mapping = {
        "denied": "policy_denied",
        "deny": "policy_denied",
        "approval_required": "approval_required",
        "ok": "allowed",
        "allow": "allowed",
        "execution_started": "allowed",
        "plan": "allowed",
    }
    return mapping.get(str(code or "").strip().lower(), "degraded")


def capability_policy_state(capability: dict) -> str:
    if bool((capability or {}).get("approval_required")):
        return "approval_required"
    return render_policy_state(str((capability or {}).get("effective_decision") or (capability or {}).get("default_policy_state") or "degraded"))
