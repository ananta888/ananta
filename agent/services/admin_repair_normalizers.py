from __future__ import annotations

from typing import Any

from agent.services.admin_repair_taxonomy import ALLOWED_EVIDENCE_SOURCES


def _to_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_platform_target(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "auto": "unknown",
        "windows": "windows11",
        "windows11": "windows11",
        "win11": "windows11",
        "ubuntu": "ubuntu",
        "ubuntu22": "ubuntu",
        "ubuntu24": "ubuntu",
        "linux": "ubuntu",
    }
    return aliases.get(raw, "unknown")


def _normalize_execution_scope(value: Any, *, platform_supported: bool) -> str:
    raw = str(value or "").strip().lower()
    if raw not in {"diagnosis_only", "bounded_repair"}:
        return "bounded_repair" if platform_supported else "diagnosis_only"
    if raw == "bounded_repair" and not platform_supported:
        return "diagnosis_only"
    return raw


def _normalize_evidence_sources(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []

    normalized: list[str] = []
    for item in raw_items:
        if not item:
            continue
        if item not in ALLOWED_EVIDENCE_SOURCES:
            continue
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        normalized = ["error_logs", "service_status", "runtime_state"]
    return normalized[:8]


def _normalize_targets(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return [item for item in items if item][:6]
