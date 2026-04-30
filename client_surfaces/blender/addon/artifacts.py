from __future__ import annotations


def normalize_artifacts(items: list[dict] | None) -> list[dict]:
    return [dict(i) for i in list(items or [])]
