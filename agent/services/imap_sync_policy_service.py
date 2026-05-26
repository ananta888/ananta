from __future__ import annotations

from typing import Any


def build_imap_sync_plan(
    *,
    sync_policy: str,
    total_available: int,
    requested_limit: int = 100,
    recent_days: int = 14,
) -> dict[str, Any]:
    mode = str(sync_policy or "manual").strip() or "manual"
    limit = max(0, int(requested_limit))
    available = max(0, int(total_available))
    if mode == "manual":
        return {
            "sync_policy": "manual",
            "should_sync": False,
            "header_limit": 0,
            "include_body": False,
            "date_window_days": 0,
            "reason_code": "manual_sync_only",
        }
    if mode == "headers_only":
        return {
            "sync_policy": "headers_only",
            "should_sync": True,
            "header_limit": min(max(1, limit), available if available else max(1, limit)),
            "include_body": False,
            "date_window_days": 0,
            "reason_code": "headers_only",
        }
    if mode == "limited_recent":
        capped = min(max(1, limit), available if available else max(1, limit))
        return {
            "sync_policy": "limited_recent",
            "should_sync": True,
            "header_limit": capped,
            "include_body": False,
            "date_window_days": max(1, int(recent_days)),
            "reason_code": "limited_recent",
        }
    raise ValueError("imap_sync_policy_unknown")
