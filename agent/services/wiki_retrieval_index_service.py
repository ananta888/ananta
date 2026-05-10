from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.retrieval.wiki_fts_store import WikiFtsStore
from worker.retrieval.wiki_hybrid_engine import merge_wiki_hybrid_results
from worker.retrieval.wiki_vector_store import WikiVectorStore


class WikiRetrievalIndexService:
    """Thin composition layer for wiki FTS/vector/hybrid retrieval."""

    def __init__(self, *, fts_db_path: Path, vector_index_path: Path) -> None:
        self._fts = WikiFtsStore(db_path=fts_db_path)
        self._vector = WikiVectorStore(index_path=vector_index_path)

    def hybrid_search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        fts = self._fts.search(query=query, top_k=top_k)
        vector = self._vector.search(query=query, top_k=top_k)
        return merge_wiki_hybrid_results(fts=fts, vector=vector, graph=[])[:top_k]
