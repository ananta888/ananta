from __future__ import annotations

from typing import Any


_TEMPLATE_GROUPS: tuple[tuple[str, str], ...] = (
    ("Blueprints", "blueprint"),
    ("Prompt-Templates", "template"),
    ("System-Prompts", "system_prompt"),
)


def grouped_template_items(payload: dict[str, Any]) -> list[tuple[str, list[tuple[int, dict[str, Any]]]]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    grouped: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = []
    for label, kind in _TEMPLATE_GROUPS:
        rows: list[tuple[int, dict[str, Any]]] = []
        for index, raw in enumerate(items):
            if isinstance(raw, dict) and str(raw.get("kind") or "") == kind:
                rows.append((index, raw))
        if rows:
            grouped.append((label, rows))
    return grouped


def template_nav_items(payload: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    ordered: list[tuple[int, dict[str, Any]]] = []
    for _, rows in grouped_template_items(payload):
        ordered.extend(rows)
    return ordered
