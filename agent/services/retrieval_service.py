from __future__ import annotations

from pathlib import Path

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service


class RetrievalService:
    """Owns retrieval-engine lifecycle and exposes a stable retrieval seam."""

    def __init__(self, knowledge_index_retrieval_service=None) -> None:
        self._orchestrator: HybridOrchestrator | None = None
        self._signature: tuple | None = None
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service or get_knowledge_index_retrieval_service()

    def _config_signature(self) -> tuple:
        return (
            settings.rag_enabled,
            settings.rag_repo_root,
            settings.rag_data_roots,
            settings.rag_max_context_chars,
            settings.rag_max_context_tokens,
            settings.rag_max_chunks,
            settings.rag_agentic_max_commands,
            settings.rag_agentic_timeout_seconds,
            settings.rag_semantic_persist_dir,
            settings.rag_redact_sensitive,
        )

    def _build_orchestrator(self) -> HybridOrchestrator:
        repo_root = Path(settings.rag_repo_root).resolve()
        data_roots = [repo_root / p.strip() for p in settings.rag_data_roots.split(",") if p.strip()]
        persist_dir = repo_root / settings.rag_semantic_persist_dir
        return HybridOrchestrator(
            repo_root=repo_root,
            data_roots=data_roots,
            max_context_chars=settings.rag_max_context_chars,
            max_context_tokens=settings.rag_max_context_tokens,
            max_chunks=settings.rag_max_chunks,
            agentic_max_commands=settings.rag_agentic_max_commands,
            agentic_timeout_seconds=settings.rag_agentic_timeout_seconds,
            semantic_persist_dir=persist_dir,
            redact_sensitive=settings.rag_redact_sensitive,
        )

    def _knowledge_index_plan(self, query: str) -> tuple[int, str]:
        normalized = str(query or "").lower()
        doc_markers = ("doc", "docs", "readme", "guide", "architecture", "policy", "adr", "concept", "overview")
        code_markers = ("bug", "error", "trace", "stack", "code", "function", "class", "module", "refactor")
        if any(marker in normalized for marker in doc_markers):
            return settings.rag_max_chunks, "doc_or_architecture_query"
        if any(marker in normalized for marker in code_markers):
            return max(2, settings.rag_max_chunks // 2), "code_or_debug_query"
        return max(1, settings.rag_max_chunks // 3), "default_balanced_query"

    def get_orchestrator(self) -> HybridOrchestrator:
        signature = self._config_signature()
        if self._orchestrator is None or self._signature != signature:
            self._orchestrator = self._build_orchestrator()
            self._signature = signature
        return self._orchestrator

    def _deserialize_chunk(self, payload: dict[str, object]) -> ContextChunk:
        return ContextChunk(
            engine=str(payload.get("engine") or ""),
            source=str(payload.get("source") or ""),
            content=str(payload.get("content") or ""),
            score=float(payload.get("score") or 0.0),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _serialize_context(
        self,
        *,
        orchestrator: HybridOrchestrator,
        query: str,
        strategy: dict[str, object],
        chunks: list[ContextChunk],
    ) -> dict[str, object]:
        serialized_chunks = []
        context_lines: list[str] = []
        for chunk in chunks:
            safe_content = orchestrator._redact(chunk.content)
            context_lines.append(f"[{chunk.engine}] {chunk.source}\n{safe_content}")
            serialized_chunks.append(
                {
                    "engine": chunk.engine,
                    "source": chunk.source,
                    "score": round(chunk.score, 3),
                    "content": safe_content,
                    "metadata": chunk.metadata,
                }
            )
        context_text = "\n\n".join(context_lines)
        return {
            "query": query,
            "strategy": strategy,
            "policy_version": orchestrator.context_manager.policy_version,
            "chunks": serialized_chunks,
            "context_text": context_text,
            "token_estimate": orchestrator.context_manager.estimate_tokens(context_text),
        }

    def retrieve_context(self, query: str) -> dict[str, object]:
        orchestrator = self.get_orchestrator()
        context_payload = orchestrator.get_relevant_context(query)
        knowledge_top_k, knowledge_reason = self._knowledge_index_plan(query)
        knowledge_chunks = self._knowledge_index_retrieval_service.search(query, top_k=knowledge_top_k)
        if not knowledge_chunks:
            strategy = dict(context_payload.get("strategy") or {})
            strategy["knowledge_index"] = 0
            strategy["knowledge_index_reason"] = knowledge_reason
            context_payload["strategy"] = strategy
            return context_payload

        orchestrator_chunks = [
            self._deserialize_chunk(chunk_payload)
            for chunk_payload in context_payload.get("chunks", [])
            if isinstance(chunk_payload, dict)
        ]
        merged = orchestrator.context_manager.rerank(
            chunks=[*orchestrator_chunks, *knowledge_chunks],
            query=query,
            max_chunks=settings.rag_max_chunks,
            max_chars=settings.rag_max_context_chars,
            max_tokens=settings.rag_max_context_tokens,
        )
        strategy = dict(context_payload.get("strategy") or {})
        strategy["knowledge_index"] = len(knowledge_chunks)
        strategy["knowledge_index_reason"] = knowledge_reason
        return self._serialize_context(
            orchestrator=orchestrator,
            query=query,
            strategy=strategy,
            chunks=merged,
        )


retrieval_service = RetrievalService()


def get_retrieval_service() -> RetrievalService:
    return retrieval_service
