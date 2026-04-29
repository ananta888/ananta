from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from worker.retrieval.embedding_provider import EmbeddingProvider


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) != len(right):
        return 0.0
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return float(numerator / (left_norm * right_norm))


class CodeCompassVectorStore:
    def __init__(self, *, index_path: str | Path):
        self._index_path = Path(index_path)

    @property
    def index_path(self) -> Path:
        return self._index_path

    def diagnostics(self) -> dict[str, Any]:
        return {"status": "ready", "reason": "json_vector_store"}

    def load(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"state": {}, "entries": []}
        payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        entries = [item for item in list(payload.get("entries") or []) if isinstance(item, dict)]
        return {"state": dict(payload.get("state") or {}), "entries": entries}

    def save(self, *, state: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"state": dict(state or {}), "entries": list(entries or [])}
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> dict[str, Any]:
        texts = [str(item.get("embedding_text") or "") for item in list(documents or [])]
        vectors = embedding_provider.embed_texts(texts)
        entries: list[dict[str, Any]] = []
        for doc, vector in zip(list(documents or []), vectors, strict=False):
            entries.append(
                {
                    "record_id": str(doc.get("record_id") or ""),
                    "kind": str(doc.get("kind") or ""),
                    "file": str(doc.get("file") or ""),
                    "parent_id": str(doc.get("parent_id") or ""),
                    "role_labels": [str(item) for item in list(doc.get("role_labels") or []) if str(item).strip()],
                    "importance_score": float(doc.get("importance_score") or 0.0),
                    "source_scope": str(doc.get("source_scope") or "repo"),
                    "profile_name": str(doc.get("profile_name") or "default"),
                    "source_manifest_hash": str(doc.get("manifest_hash") or manifest_hash or ""),
                    "embedding_text": str(doc.get("embedding_text") or ""),
                    "vector": [float(item) for item in list(vector or [])],
                }
            )
        state = {
            "schema": "codecompass_vector_index.v1",
            "retrieval_cache_state": str(retrieval_cache_state or ""),
            "manifest_hash": str(manifest_hash or ""),
            "embedding_provider": str(getattr(embedding_provider, "provider_id", "unknown") or "unknown"),
            "embedding_model_name": str(getattr(embedding_provider, "model_version", "unknown") or "unknown"),
            "embedding_dimensions": int(getattr(embedding_provider, "dimensions", 0) or 0),
            "entry_count": len(entries),
        }
        self.save(state=state, entries=entries)
        return {"status": "ok", "mode": "rebuild", "indexed_documents": len(entries), "state": state}

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> dict[str, Any]:
        current = self.load()
        state = dict(current.get("state") or {})
        changed = (
            str(state.get("retrieval_cache_state") or "") != str(retrieval_cache_state or "")
            or str(state.get("manifest_hash") or "") != str(manifest_hash or "")
            or str(state.get("embedding_model_name") or "") != str(getattr(embedding_provider, "model_version", "unknown") or "")
            or int(state.get("embedding_dimensions") or 0) != int(getattr(embedding_provider, "dimensions", 0) or 0)
        )
        if not changed:
            return {"status": "ok", "mode": "unchanged", "indexed_documents": 0, "state": state}
        return self.rebuild(
            documents=documents,
            embedding_provider=embedding_provider,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def search_by_vector(self, *, query_vector: list[float], top_k: int = 10) -> list[dict[str, Any]]:
        loaded = self.load()
        entries = [dict(item) for item in list(loaded.get("entries") or []) if isinstance(item, dict)]
        ranked: list[dict[str, Any]] = []
        for entry in entries:
            vector = [float(item) for item in list(entry.get("vector") or [])]
            score = _cosine_similarity(query_vector, vector)
            ranked.append({**entry, "vector_score": score, "score": score})
        ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return ranked[: max(1, int(top_k))]

    def search(
        self,
        *,
        query: str,
        embedding_provider: EmbeddingProvider,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        vectors = embedding_provider.embed_texts([str(query or "")])
        query_vector = [float(item) for item in list(vectors[0] if vectors else [])]
        return self.search_by_vector(query_vector=query_vector, top_k=top_k)

