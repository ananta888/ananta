from __future__ import annotations

from typing import Any


def build_goal_rows(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else []
    if not items:
        return ["no goals available"]
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(f"{item.get('id', '-')} [{item.get('status', 'unknown')}] {item.get('title', item.get('summary', ''))}")
    return rows or ["no goals available"]


def build_task_rows(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else []
    if not items:
        return ["no tasks available"]
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"{item.get('id', '-')} [{item.get('status', 'unknown')}] "
            f"agent={item.get('agent', '-')} {item.get('title', item.get('summary', ''))}"
        )
    return rows or ["no tasks available"]


def build_inspection_detail(section_id: str, payload: dict[str, Any], selected_index: int) -> list[str]:
    if section_id == "goals":
        rows = build_goal_rows(payload)
    elif section_id == "tasks":
        rows = build_task_rows(payload)
    else:
        rows = [f"{key}={value}" for key, value in sorted(payload.items())]
    index = max(0, min(int(selected_index), max(0, len(rows) - 1)))
    return [f"inspect_index={index}", rows[index] if rows else "empty"]
