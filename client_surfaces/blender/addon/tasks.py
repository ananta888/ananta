from __future__ import annotations


def normalize_tasks(items: list[dict] | None) -> list[dict]:
    return [dict(i) for i in list(items or [])]
