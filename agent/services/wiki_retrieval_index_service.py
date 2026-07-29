"""Wiki lexical/vector retrieval composed independently from index mutation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.wiki_vector_index_mutation_service import (
    DelegatedWikiVectorMutationService,
    LocalWikiVectorMutationService,
    VectorIndexInputPublisherPort,
    VectorIndexTaskSubmissionPort,
    WikiVectorMutationPort,
)
from worker.retrieval.embedding_provider import HashEmbeddingProvider
from worker.retrieval.wiki_fts_store import WikiFtsStore
from worker.retrieval.wiki_hybrid_engine import (
    merge_wiki_hybrid_results,
)
from worker.retrieval.wiki_vector_store import (
    WikiVectorStore,
    WikiVectorStoreConfig,
)


class WikiRetrievalIndexService:
    """Compose read-only Wiki retrieval with a narrow mutation capability."""

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
        index_task_service: VectorIndexTaskSubmissionPort | None = None,
        index_input_publisher: VectorIndexInputPublisherPort | None = None,
        embedding_provider_config: Mapping[str, Any] | None = None,
        allow_hub_qdrant_reads: bool = False,
        index_task_actor: str = "wiki-vector-service",
        mutation_service: WikiVectorMutationPort | None = None,
    ) -> None:
        self._fts = WikiFtsStore(db_path=fts_db_path)
        effective_config = vector_config or getattr(vector_store, "config", None) or WikiVectorStoreConfig()
        if effective_config.provider == "qdrant" and not allow_hub_qdrant_reads:
            raise ValueError("wiki_vector_hub_qdrant_read_not_enabled")
        self._vector = vector_store or WikiVectorStore(
            index_path=vector_index_path,
            config=effective_config,
            secret_resolver=vector_secret_resolver,
            observer=vector_observer,
        )
        self._vector_config = effective_config
        self._embedding_provider = embedding_provider or HashEmbeddingProvider(
            provider_id="wiki_local_hash",
            model_version="wiki-hash-v1",
            dimensions=12,
        )
        self._mutations = mutation_service or self._build_mutations(
            index_task_service=index_task_service,
            index_input_publisher=index_input_publisher,
            embedding_provider_config=embedding_provider_config,
            index_task_actor=index_task_actor,
        )
        self._closed = False

    def hybrid_search(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        fts = self._fts.search(query=query, top_k=top_k)
        vector = self._vector.search(
            query=query,
            embedding_provider=self._embedding_provider,
            top_k=top_k,
        )
        return merge_wiki_hybrid_results(
            fts=fts,
            vector=vector,
            graph=[],
        )[:top_k]

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._mutations.refresh(
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._mutations.rebuild(
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def delete(
        self,
        *,
        record_ids: list[str],
    ) -> Mapping[str, Any]:
        return self._mutations.delete(record_ids=record_ids)

    def close(self) -> None:
        """Release the vector backend owned by this retrieval facade."""

        if self._closed:
            return
        self._closed = True
        self._vector.close()

    def _build_mutations(
        self,
        *,
        index_task_service: VectorIndexTaskSubmissionPort | None,
        index_input_publisher: VectorIndexInputPublisherPort | None,
        embedding_provider_config: Mapping[str, Any] | None,
        index_task_actor: str,
    ) -> WikiVectorMutationPort:
        if self._vector_config.provider == "json":
            return LocalWikiVectorMutationService(
                vector_store=self._vector,
                embedding_provider=self._embedding_provider,
            )
        if index_task_service is None:
            raise ValueError("vector_index_delegation_required")
        if index_input_publisher is None:
            raise ValueError("vector_index_input_publisher_required")
        return DelegatedWikiVectorMutationService(
            vector_config=self._vector_config,
            embedding_provider=self._embedding_provider,
            index_task_service=index_task_service,
            index_input_publisher=index_input_publisher,
            embedding_provider_config=embedding_provider_config,
            index_task_actor=index_task_actor,
        )


__all__ = ["WikiRetrievalIndexService"]
