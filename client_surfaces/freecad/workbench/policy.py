from __future__ import annotations

from typing import Any


SENSITIVE_CAPABILITIES = {"freecad.model.mutate", "freecad.macro.execute"}


def describe_policy_response(response: dict[str, Any] | None, *, capability_id: str) -> dict[str, Any]:
    payload = dict(response or {})
    status = str(payload.get("status") or "degraded").strip().lower()
    if status in {"denied", "policy_denied"}:
        ui_state = "denied"
    elif status in {"approval_required", "pending"}:
        ui_state = "approval_required"
    elif capability_id in SENSITIVE_CAPABILITIES and status == "accepted":
        ui_state = "approval_required"
    else:
        ui_state = status or "degraded"
    return {"ui_state": ui_state, "status": status, "reason": payload.get("reason")}
