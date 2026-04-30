from __future__ import annotations

from typing import Any


def evaluate_health_state(health_payload: dict[str, Any], capabilities_payload: dict[str, Any]) -> dict[str, Any]:
    health_status = str(health_payload.get("status") or "degraded")
    capabilities = [str(item) for item in list(capabilities_payload.get("capabilities") or []) if str(item).strip()]
    if health_status in {"connected", "ok"} and capabilities:
        state = "connected"
    elif health_status in {"unauthorized", "forbidden"}:
        state = "unauthorized"
    elif capabilities and health_status == "policy_limited":
        state = "policy_limited"
    else:
        state = "degraded"
    return {"state": state, "health_status": health_status, "capabilities": capabilities}
