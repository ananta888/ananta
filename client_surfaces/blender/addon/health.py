from __future__ import annotations


def evaluate_health(connected: bool, capabilities: list[str] | None = None) -> dict:
    return {
        "connected": bool(connected),
        "capabilities": list(capabilities or []),
        "state": "connected" if connected else "degraded",
    }
