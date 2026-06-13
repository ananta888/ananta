from __future__ import annotations

from typing import Any

from worker.retrieval.embedding_provider import EmbeddingProvider, EmbeddingProviderError
from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore

_TASK_KIND_WEIGHT = {
    "bugfix": 1.0,
    "refactor": 1.1,
    "architecture": 1.2,
    "config": 1.05,
}

_INTENT_WEIGHT = {
    "fuzzy_semantic": 1.2,
    "architecture": 1.15,
    "exact_symbol": 0.9,
}


class CodeCompassVectorEngine:
    def __init__(
        self,
        *,
        store: CodeCompassVectorStore,
        embedding_provider: EmbeddingProvider | None,
        degraded_reason: str | None = None,
    ):
        self._store = store
        self._embedding_provider = embedding_provider
        self._last_diagnostic: dict[str, Any] = (
            {"status": "degraded", "reason": degraded_reason}
            if degraded_reason
            else {"status": "ready", "reason": "ok"}
        )

    def last_diagnostic(self) -> dict[str, Any]:
        return dict(self._last_diagnostic)

    def search(
        self,
        *,
        query: str,
        top_k: int = 10,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._embedding_provider is None:
            self._last_diagnostic = {"status": "degraded", "reason": "provider_resolution_failed"}
            return []
        task_weight = float(_TASK_KIND_WEIGHT.get(str(task_kind or "").strip().lower(), 1.0))
        intent_weight = float(_INTENT_WEIGHT.get(str(retrieval_intent or "").strip().lower(), 1.0))
        try:
            rows = self._store.search(
                query=str(query or ""),
                embedding_provider=self._embedding_provider,
                top_k=max(1, int(top_k)),
            )
            self._last_diagnostic = {"status": "ready", "reason": "ok", "candidate_count": len(rows)}
        except EmbeddingProviderError as exc:
            self._last_diagnostic = {"status": "degraded", "reason": "embedding_provider_failure", "error": str(exc)}
            return []
        state = dict((self._store.load().get("state") or {}))
        model_name = str(state.get("embedding_model_name") or getattr(self._embedding_provider, "model_version", "unknown"))
        manifest_hash = str(state.get("manifest_hash") or "")
        weighted: list[dict[str, Any]] = []
        for row in rows:
            vector_score = float(row.get("vector_score") or row.get("score") or 0.0)
            final_score = vector_score * task_weight * intent_weight
            weighted.append(
                {
                    "engine": "codecompass_vector",
                    "source": str(row.get("file") or ""),
                    "content": str(row.get("embedding_text") or "")[:320],
                    "score": final_score,
                    "record_id": str(row.get("record_id") or ""),
                    "metadata": {
                        "record_id": str(row.get("record_id") or ""),
                        "record_kind": str(row.get("kind") or ""),
                        "file": str(row.get("file") or ""),
                        "vector_score": vector_score,
                        "model_name": model_name,
                        "source_manifest_hash": manifest_hash or str(row.get("source_manifest_hash") or ""),
                        "task_kind_weight": task_weight,
                        "retrieval_intent_weight": intent_weight,
                    },
                }
            )
        weighted.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return weighted[: max(1, int(top_k))]

    @classmethod
    def build_from_config(
        cls,
        store: CodeCompassVectorStore,
        *,
        scope: str = "codecompass_vector",
        provider_config: dict[str, Any] | None = None,
    ) -> "CodeCompassVectorEngine":
        """EPC-009: Build engine using EmbeddingProviderConfigService."""
        try:
            from agent.services.embedding_provider_config_service import (
                EmbeddingProviderConfigService,
                build_embedding_provider_from_config,
            )
            svc = EmbeddingProviderConfigService(global_config=provider_config or {})
            cfg = svc.resolve(scope)
            provider = build_embedding_provider_from_config(cfg)
        except Exception:
            return cls(
                store=store,
                embedding_provider=None,
                degraded_reason="provider_resolution_failed",
            )
        return cls(store=store, embedding_provider=provider)
