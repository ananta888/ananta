"""Thin CodeCompass retriever facade for workflow adapters (LCG-009).

The only allowed retriever source; wraps the existing HybridRetrievalService.
Returns a simplified dict so adapters stay decoupled from retrieval internals.
"""
from __future__ import annotations

from typing import Any


class CodeCompassRetriever:
    """Query CodeCompass for context to inject into LangChain/LangGraph chains."""

    def query(self, query: str, *, max_results: int = 5) -> dict[str, Any]:
        """Return {sources: [...], query, metadata} without crashing if index is cold."""
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
            "metadata": {"max_results": max_results, "source": "codecompass"},
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
