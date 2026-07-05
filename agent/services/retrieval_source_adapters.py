from __future__ import annotations

from typing import Any, Callable

from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.services.retrieval_source_contract import RetrievalSourceAdapter, normalize_chunk_metadata


class RepoRetrievalSourceAdapter(RetrievalSourceAdapter):
    source_type = "repo"

    def __init__(
        self,
        *,
        orchestrator_provider: Callable[[], HybridOrchestrator],
        chunk_deserializer: Callable[[dict[str, object]], ContextChunk],
    ) -> None:
        self._orchestrator_provider = orchestrator_provider
        self._chunk_deserializer = chunk_deserializer

    def load_context(self, query: str, *, domain_scope: object | None = None) -> dict[str, object]:
        return self._orchestrator_provider().get_relevant_context(query, domain_scope=domain_scope)

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        context_payload: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> list[ContextChunk]:
        del query, task_kind, retrieval_intent, kwargs
        payload = context_payload if isinstance(context_payload, dict) else {}
        chunks = []
        for chunk_payload in payload.get("chunks", []):
            if isinstance(chunk_payload, dict):
                chunks.append(self._chunk_deserializer(chunk_payload))
        return chunks[: max(1, int(top_k or 1))]


class ArtifactKnowledgeSourceAdapter(RetrievalSourceAdapter):
    source_type = "artifact"

    def __init__(self, knowledge_index_retrieval_service) -> None:
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[ContextChunk]:
        return self._knowledge_index_retrieval_service.search(
            query,
            top_k=top_k,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes={"artifact"},
        )


class WikiKnowledgeSourceAdapter(RetrievalSourceAdapter):
    source_type = "wiki"

    def __init__(self, knowledge_index_retrieval_service) -> None:
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[ContextChunk]:
        return self._knowledge_index_retrieval_service.search(
            query,
            top_k=top_k,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes={"wiki"},
        )


class OpenNotebookKnowledgeSourceAdapter(RetrievalSourceAdapter):
    """Retrieves chunks from imported OpenNotebook knowledge indices.

    Reuses the KnowledgeIndexRetrievalService search over records with
    source_scope='open_notebook' and lifts the importer's import_metadata into
    normalized chunk metadata. Chat session content is ignored defensively;
    note chunks are slightly down-weighted against primary sources.
    """

    source_type = "open_notebook"

    NOTE_SCORE_MULTIPLIER = 0.85
    _LIFTED_METADATA_KEYS = (
        "source_system",
        "registry_source_id",
        "open_notebook_source_id",
        "snapshot_id",
        "artifact_id",
        "record_kind",
        "note_type",
        "derived_from",
        "parent_source_id",
        "parent_artifact_id",
        "parent_source_snapshot_id",
        "transformation_name",
        "insight_type",
        "canonical_url",
        "file_path",
        "content_hash",
        "import_key",
        "source_title",
        "collection_names",
        "notebook_ids",
        "retrieval_priority",
    )

    def __init__(self, knowledge_index_retrieval_service) -> None:
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[ContextChunk]:
        raw_chunks = self._knowledge_index_retrieval_service.search(
            query,
            top_k=max(1, int(top_k or 1)),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes={"open_notebook"},
        )
        chunks: list[ContextChunk] = []
        for chunk in raw_chunks:
            metadata = dict(chunk.metadata or {})
            import_metadata = dict(metadata.get("import_metadata") or {})
            record_kind = str(import_metadata.get("record_kind") or metadata.get("record_kind") or "").strip()
            if record_kind == "chat_session":
                continue
            for key in self._LIFTED_METADATA_KEYS:
                value = import_metadata.get(key)
                if value is not None:
                    metadata[key] = value
            metadata["source_type"] = self.source_type
            metadata["source_scope"] = "open_notebook"
            metadata = normalize_chunk_metadata(
                engine=chunk.engine,
                source=chunk.source,
                content=chunk.content,
                metadata=metadata,
            )
            score = float(chunk.score or 0.0)
            if str(metadata.get("record_kind") or "") == "note":
                score *= self.NOTE_SCORE_MULTIPLIER
            chunks.append(
                ContextChunk(
                    engine=chunk.engine,
                    source=chunk.source,
                    content=chunk.content,
                    score=score,
                    metadata=metadata,
                )
            )
        chunks.sort(key=lambda item: (-item.score, item.engine, item.source, item.content[:80]))
        return chunks[: max(1, int(top_k or 1))]


class TaskMemorySourceAdapter(RetrievalSourceAdapter):
    source_type = "task_memory"

    def __init__(
        self,
        *,
        memory_search: Callable[..., tuple[list[ContextChunk], dict[str, object]]],
    ) -> None:
        self._memory_search = memory_search

    def search_with_meta(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
    ) -> tuple[list[ContextChunk], dict[str, object]]:
        del task_kind, retrieval_intent
        return self._memory_search(
            query=query,
            task_id=task_id,
            goal_id=goal_id,
            neighbor_task_ids=neighbor_task_ids,
            top_k=top_k,
        )

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[ContextChunk]:
        chunks, _meta = self.search_with_meta(
            query,
            top_k=top_k,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            task_id=kwargs.get("task_id"),
            goal_id=kwargs.get("goal_id"),
            neighbor_task_ids=kwargs.get("neighbor_task_ids"),
        )
        return chunks
