from __future__ import annotations


def render_policy_state(code: str) -> str:
    mapping={"denied":"policy_denied","approval_required":"approval_required","ok":"allowed"}
    return mapping.get(str(code),"degraded")
