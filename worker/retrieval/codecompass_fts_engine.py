from __future__ import annotations

from typing import Any

from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore

_TASK_KIND_WEIGHT = {
    "bugfix": 1.25,
    "refactor": 1.15,
    "architecture": 1.05,
    "config": 1.1,
}

_INTENT_WEIGHT = {
    "exact_symbol": 1.25,
    "config_lookup": 1.15,
    "architecture": 1.05,
}


class CodeCompassFtsEngine:
    def __init__(self, *, store: CodeCompassFtsStore):
        self._store = store

    def search(
        self,
        *,
        query: str,
        top_k: int = 10,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._store.search(query=query, top_k=top_k)
        task_weight = float(_TASK_KIND_WEIGHT.get(str(task_kind or "").strip().lower(), 1.0))
        intent_weight = float(_INTENT_WEIGHT.get(str(retrieval_intent or "").strip().lower(), 1.0))
        weighted: list[dict[str, Any]] = []
        for row in rows:
            final_score = float(row.get("score") or 0.0) * task_weight * intent_weight
            weighted.append(
                {
                    "engine": "codecompass_fts",
                    "source": str(row.get("file") or ""),
                    "content": "",
                    "score": final_score,
                    "metadata": {
                        "record_id": str(row.get("record_id") or ""),
                        "record_kind": str(row.get("kind") or ""),
                        "file": str(row.get("file") or ""),
                        "bm25_score": float(row.get("bm25_score") or 0.0),
                        "field_boost_breakdown": dict(row.get("boost_breakdown") or {}),
                        "source_manifest_hash": str(row.get("source_manifest_hash") or ""),
                        "task_kind_weight": task_weight,
                        "retrieval_intent_weight": intent_weight,
                    },
                }
            )
        weighted.sort(key=lambda item: float(item["score"]), reverse=True)
        return weighted[: max(1, int(top_k))]

