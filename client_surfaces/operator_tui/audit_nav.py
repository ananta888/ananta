from __future__ import annotations

from typing import Any


_AUDIT_GROUPS: tuple[str, ...] = (
    "Audit Logs",
    "Runtime Telemetry",
    "LLM/Debug",
    "Task/Ops",
)


def grouped_audit_items(payload: dict[str, Any]) -> list[tuple[str, list[tuple[int, dict[str, Any]]]]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    grouped: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = []
    for group in _AUDIT_GROUPS:
        rows: list[tuple[int, dict[str, Any]]] = []
        for index, raw in enumerate(items):
            if isinstance(raw, dict) and str(raw.get("group") or "") == group:
                rows.append((index, raw))
        if rows:
            grouped.append((group, rows))
    known = {group for group, _ in grouped}
    extra_rows: list[tuple[int, dict[str, Any]]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        group = str(raw.get("group") or "")
        if group and group not in known:
            extra_rows.append((index, raw))
    if extra_rows:
        grouped.append(("Other", extra_rows))
    return grouped


def audit_nav_items(payload: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    ordered: list[tuple[int, dict[str, Any]]] = []
    for _, rows in grouped_audit_items(payload):
        ordered.extend(rows)
    return ordered
