from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worker.retrieval.codecompass_fts_engine import CodeCompassFtsEngine
from worker.retrieval.sira.service import SiraRetrievalService


class SiraFtsChannelProvider:
    """OCP adapter: SIRA remains the existing ``codecompass_fts`` channel."""

    def __init__(self, *, baseline: CodeCompassFtsEngine, sira: SiraRetrievalService):
        self._baseline = baseline
        self._sira = sira
        self._last_trace: dict[str, Any] = {}

    @property
    def last_trace(self) -> Mapping[str, Any]:
        return dict(self._last_trace)

    def search(
        self,
        *,
        query: str,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._baseline.search(
            query=query,
            top_k=top_k,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
        )
        return [dict(item) for item in rows]

    def search_profiled(
        self,
        *,
        query: str,
        top_k: int,
        retrieval_profile: Mapping[str, Any],
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        del task_kind, retrieval_intent
        result = self._sira.retrieve(
            query=query,
            top_k=top_k,
            corpus_ready=bool(retrieval_profile.get("corpus_ready", True)),
            baseline_margin=(
                float(retrieval_profile["baseline_margin"])
                if retrieval_profile.get("baseline_margin") is not None
                else None
            ),
            expansion_cached=bool(retrieval_profile.get("expansion_cached", False)),
            model_budget_available=bool(retrieval_profile.get("model_budget_available", True)),
        )
        self._last_trace = dict(result.get("trace") or {})
        selected: list[dict[str, Any]] = []
        for row in list(result.get("selected_candidates") or []):
            item = dict(row)
            metadata = dict(item.get("metadata") or {})
            metadata["sira_trace"] = self._last_trace
            selected.append({**item, "metadata": metadata})
        return selected
