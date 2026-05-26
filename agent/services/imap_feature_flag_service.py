from __future__ import annotations

from typing import Any


def resolve_imap_feature_flags(config: dict[str, Any] | None) -> dict[str, Any]:
    candidate = dict(config or {})
    imap_cfg = dict(candidate.get("imap") or {})
    return {
        "enabled": bool(imap_cfg.get("enabled", False)),
        "sync_enabled": bool(imap_cfg.get("sync_enabled", True)),
        "sync_policy": str(imap_cfg.get("sync_policy") or "manual"),
    }


def resolve_imap_runtime_state(
    config: dict[str, Any] | None,
    *,
    has_account: bool,
    connected: bool,
    syncing: bool,
) -> dict[str, str]:
    flags = resolve_imap_feature_flags(config)
    if not flags["enabled"] or not has_account:
        return {"state": "disabled", "reason_code": "feature_disabled_or_no_account"}
    if syncing and bool(flags["sync_enabled"]):
        return {"state": "syncing", "reason_code": "sync_in_progress"}
    if connected:
        return {"state": "connected", "reason_code": "connected"}
    return {"state": "offline", "reason_code": "enabled_not_connected"}
