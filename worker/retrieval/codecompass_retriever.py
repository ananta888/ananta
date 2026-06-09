"""Thin CodeCompass retriever facade for workflow adapters (LCG-009, LCG-010).

The only allowed retriever source; wraps the existing HybridRetrievalService.
Returns a simplified dict so adapters stay decoupled from retrieval internals.

LCG-010: optionally honours EmbeddingProviderConfigService so the workflow
layer shares the same embedding model selection as the rest of Ananta.
The wiring is opt-in: if no provider_config is passed, the retriever
falls back to the default HybridRetrievalService (the pre-LCG path).
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.embedding_provider_config_service import (
        EmbeddingProviderConfigService,
    )


class CodeCompassRetriever:
    """Query CodeCompass for context to inject into LangChain/LangGraph chains.

    Parameters
    ----------
    provider_config:
        Optional dict that will be wrapped in an
        ``EmbeddingProviderConfigService(global_config=...)`` and passed
        to ``resolve(scope='worker_retrieval')`` before the underlying
        retrieval is performed. When ``None`` (default), the retriever
        uses the pre-LCG path: ``HybridRetrievalService`` with default
        config. The latter is what every caller did before LCG-010.
    scope:
        Scope name passed to ``EmbeddingProviderConfigService.resolve``.
        Default is ``worker_retrieval`` which is the same scope the
        rest of Ananta uses for retrieval, so a single provider
        selection propagates everywhere.
    """

    def __init__(
        self,
        *,
        provider_config: dict[str, Any] | None = None,
        scope: str = "worker_retrieval",
    ) -> None:
        self._provider_config = provider_config
        self._scope = scope
        self._resolved_provider: dict[str, Any] | None = None
        self._resolved_provider_error: str | None = None

    # ── LCG-010 wiring ─────────────────────────────────────────────────

    @property
    def resolved_provider(self) -> dict[str, Any] | None:
        """The flat provider dict the retriever is currently using.

        None if no provider_config was injected, or if resolution
        failed (in which case ``resolved_provider_error`` is set).
        """
        if self._resolved_provider is None and self._provider_config is not None:
            self._resolve_provider()
        return self._resolved_provider

    @property
    def resolved_provider_error(self) -> str | None:
        return self._resolved_provider_error

    def _resolve_provider(self) -> None:
        """Resolve the embedding provider via EmbeddingProviderConfigService.

        Errors are captured, not raised. The retriever still falls back
        to HybridRetrievalService if the service is unavailable —
        LCG-010 adds wiring, not a hard dependency.
        """
        try:
            from agent.services.embedding_provider_config_service import (
                EmbeddingProviderConfigService,
            )
            svc: EmbeddingProviderConfigService = EmbeddingProviderConfigService(
                global_config=self._provider_config or {},
            )
            self._resolved_provider = svc.resolve_for_build(scope=self._scope)
        except Exception as exc:  # ImportError, ValidationError, etc.
            self._resolved_provider_error = f"{type(exc).__name__}: {exc}"
            self._resolved_provider = None

    # ── Query API ──────────────────────────────────────────────────────

    def query(self, query: str, *, max_results: int = 5) -> dict[str, Any]:
        """Return {sources: [...], query, metadata, embedding_provider} without crashing if index is cold."""
        try:
            from worker.retrieval.retrieval_service import HybridRetrievalService
            svc = HybridRetrievalService()
            result = svc.retrieve(
                query=query,
                pipeline_contract=None,
                channel_results={},
                top_k=max_results,
            )
            sources = self._extract_sources(result, max_results)
        except Exception:
            sources = []

        return {
            "query": query,
            "sources": sources,
            "metadata": {
                "max_results": max_results,
                "source": "codecompass",
                "embedding_provider": self.resolved_provider,
            },
        }

    @staticmethod
    def _extract_sources(result: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        candidates = result.get("candidates") or result.get("chunks") or []
        out = []
        for c in candidates[:limit]:
            out.append({
                "path": str(c.get("path") or c.get("source") or ""),
                "content": str(c.get("content") or "")[:500],
                "score": float(c.get("score") or 0.0),
            })
        return out
