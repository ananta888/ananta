from __future__ import annotations

from typing import Any, Callable

from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.services.retrieval_source_contract import RetrievalSourceAdapter


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

    def load_context(self, query: str) -> dict[str, object]:
        return self._orchestrator_provider().get_relevant_context(query)

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
