from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from worker.retrieval.sira.contracts import CompiledQuery
from worker.retrieval.sira.enriched_fts_store import EnrichedFtsStore


class WeightedLexicalRetriever:
    """Execute exactly one snapshot-bound FTS top-k action."""

    def __init__(self, *, store: EnrichedFtsStore):
        self._store = store

    def retrieve(self, query: CompiledQuery, *, top_k: int) -> Sequence[Mapping[str, Any]]:
        return self._store.search_weighted(query, top_k=max(1, int(top_k)))
