from __future__ import annotations


def approval_state(item: dict) -> str:
    return str((item or {}).get("state") or "pending")
