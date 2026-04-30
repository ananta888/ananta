from __future__ import annotations

from typing import Any


def approval_state(item: dict[str, Any] | None) -> str:
    state = str(((item or {}).get("state") or "pending")).strip().lower()
    if state in {"approved", "rejected", "pending", "denied"}:
        return state
    return "pending"


def can_execute_locally(item: dict[str, Any] | None) -> bool:
    state = approval_state(item)
    return state == "approved" and bool((item or {}).get("approval_id"))
