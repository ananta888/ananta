from __future__ import annotations

from typing import Any


def merge_wiki_hybrid_results(*, fts: list[dict[str, Any]], vector: list[dict[str, Any]], graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic additive merge with score explanation fields."""
    merged: dict[str, dict[str, Any]] = {}
    for channel, rows, weight in (("fts", fts, 1.0), ("vector", vector, 0.9), ("graph", graph, 0.4)):
        for row in rows:
            key = str(row.get("chunk_id") or row.get("source") or row.get("id") or "")
            if not key:
                continue
            current = merged.get(key, {"hybrid_score": 0.0, "score_components": {}, **row})
            score = float(row.get("score") or 0.0)
            current["hybrid_score"] = float(current.get("hybrid_score") or 0.0) + (score * weight)
            current["score_components"][channel] = score
            merged[key] = current
    return sorted(merged.values(), key=lambda item: float(item.get("hybrid_score") or 0.0), reverse=True)
