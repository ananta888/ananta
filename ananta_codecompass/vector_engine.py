from __future__ import annotations

from typing import Any

from worker.retrieval.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    build_embedding_provider,
)
from worker.retrieval.embedding_text_builder import build_query_embedding_text
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    VectorScope,
    VectorSearchPort,
    VectorSearchQuery,
    VectorStoreError,
    VectorStoreFailClosedError,
    VectorStoreFilters,
)

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
        store: VectorSearchPort,
        embedding_provider: EmbeddingProvider | None,
        degraded_reason: str | None = None,
        propagate_vector_store_errors: bool = False,
    ):
        self._store = store
        self._embedding_provider = embedding_provider
        self._propagate_vector_store_errors = bool(propagate_vector_store_errors)
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
        scope: VectorScope | None = None,
        filters: VectorStoreFilters | None = None,
        compatibility: CompatibilitySpec | None = None,
    ) -> list[dict[str, Any]]:
        if self._embedding_provider is None:
            self._last_diagnostic = {"status": "degraded", "reason": "provider_resolution_failed"}
            return []
        task_weight = float(_TASK_KIND_WEIGHT.get(str(task_kind or "").strip().lower(), 1.0))
        intent_weight = float(_INTENT_WEIGHT.get(str(retrieval_intent or "").strip().lower(), 1.0))
        try:
            vectors = self._embedding_provider.embed_texts([build_query_embedding_text(str(query or ""))])
            query_vector = tuple(float(item) for item in list(vectors[0] if vectors else []))
            result = self._store.search_by_vector(
                VectorSearchQuery(
                    query_vector=query_vector,
                    top_k=max(1, int(top_k)),
                    scope=scope,
                    filters=filters,
                    compatibility=compatibility,
                )
            )
            rows = [hit.as_dict() for hit in result.hits]
            status = "degraded" if result.reason in {"missing_index", "empty_index"} else "ready"
            self._last_diagnostic = {
                "status": status,
                "reason": result.reason,
                "candidate_count": len(rows),
                **dict(result.diagnostics),
            }
        except EmbeddingProviderError as exc:
            self._last_diagnostic = {"status": "degraded", "reason": "embedding_provider_failure", "error": str(exc)}
            return []
        except VectorStoreFailClosedError:
            raise
        except VectorStoreError as exc:
            self._last_diagnostic = {
                "status": "degraded",
                "reason": exc.reason,
                **dict(exc.details),
            }
            if self._propagate_vector_store_errors:
                raise
            return []
        model_name = str(
            result.diagnostics.get("model") or getattr(self._embedding_provider, "model_version", "unknown")
        )
        manifest_hash = str(result.diagnostics.get("manifest_hash") or "")
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
        store: VectorSearchPort,
        *,
        scope: str = "codecompass_vector",
        provider_config: dict[str, Any] | None = None,
    ) -> "CodeCompassVectorEngine":
        """Build a worker-local engine without importing Hub service modules."""
        try:
            payload = {"provider": "local_hash", **dict(provider_config or {})}
            provider = build_embedding_provider(payload)
        except (EmbeddingProviderError, TypeError, ValueError):
            return cls(
                store=store,
                embedding_provider=None,
                degraded_reason="provider_resolution_failed",
            )
        return cls(store=store, embedding_provider=provider)
