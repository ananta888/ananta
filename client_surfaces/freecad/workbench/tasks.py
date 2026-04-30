from __future__ import annotations


def normalize_tasks(items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(items or []):
        entry = dict(item)
        entry.setdefault("status", "unknown")
        normalized.append(entry)
    return normalized
