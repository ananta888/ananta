from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.retrieval.wiki_fts_store import WikiFtsStore
from worker.retrieval.wiki_hybrid_engine import merge_wiki_hybrid_results
from worker.retrieval.embedding_provider import HashEmbeddingProvider
from worker.retrieval.wiki_vector_store import WikiVectorStore, WikiVectorStoreConfig


class WikiRetrievalIndexService:
    """Thin composition layer for wiki FTS/vector/hybrid retrieval."""

    def __init__(
        self,
        *,
        fts_db_path: Path,
        vector_index_path: Path,
        embedding_provider: Any | None = None,
        vector_store: WikiVectorStore | None = None,
        vector_config: WikiVectorStoreConfig | None = None,
        vector_secret_resolver: Any = None,
        vector_observer: Any = None,
    ) -> None:
        self._fts = WikiFtsStore(db_path=fts_db_path)
        self._vector = vector_store or WikiVectorStore(
            index_path=vector_index_path,
            config=vector_config,
            secret_resolver=vector_secret_resolver,
            observer=vector_observer,
        )
        self._embedding_provider = embedding_provider or HashEmbeddingProvider(
            provider_id="wiki_local_hash",
            model_version="wiki-hash-v1",
            dimensions=12,
        )

    def hybrid_search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        fts = self._fts.search(query=query, top_k=top_k)
        vector = self._vector.search(
            query=query,
            embedding_provider=self._embedding_provider,
            top_k=top_k,
        )
        return merge_wiki_hybrid_results(fts=fts, vector=vector, graph=[])[:top_k]
