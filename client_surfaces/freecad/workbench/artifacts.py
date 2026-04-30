from __future__ import annotations


def normalize_artifacts(items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(items or []):
        entry = dict(item)
        entry.setdefault("artifact_type", "unknown")
        normalized.append(entry)
    return normalized
