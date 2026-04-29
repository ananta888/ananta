from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    key: str
    payload: dict[str, Any]
    created_at: float
    quality_score: float


class RetrievalCache:
    def __init__(self, *, ttl_seconds: int = 600):
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._entries: dict[str, CacheEntry] = {}

    @staticmethod
    def build_key(
        *,
        query: str,
        profile: str,
        index_version: str,
        embedding_model_version: str,
        content_hash: str,
    ) -> str:
        return "|".join(
            [
                str(query or "").strip(),
                str(profile or "balanced").strip().lower(),
                str(index_version or "").strip(),
                str(embedding_model_version or "").strip(),
                str(content_hash or "").strip(),
            ]
        )

    def get(self, *, key: str) -> dict[str, Any] | None:
        item = self._entries.get(str(key))
        if not item:
            return None
        if (time.time() - item.created_at) > self._ttl_seconds:
            self._entries.pop(str(key), None)
            return None
        return dict(item.payload)

    def put(self, *, key: str, payload: dict[str, Any], quality_score: float) -> None:
        self._entries[str(key)] = CacheEntry(
            key=str(key),
            payload=dict(payload or {}),
            created_at=time.time(),
            quality_score=float(quality_score),
        )

    def stats(self) -> dict[str, int]:
        now = time.time()
        alive = sum(1 for value in self._entries.values() if (now - value.created_at) <= self._ttl_seconds)
        return {"entry_count": alive}

