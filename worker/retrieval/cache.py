from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from worker.core.memory_authorization import WorkerMemoryAuthorizationPort, require_worker_memory


@dataclass(frozen=True)
class CacheEntry:
    key: str
    payload: dict[str, Any]
    created_at: float
    quality_score: float
    expires_at: float
    policy_digest: str
    namespace: tuple[str, ...]


class RetrievalCache:
    def __init__(self, *, ttl_seconds: int = 600, authorization: WorkerMemoryAuthorizationPort | None = None):
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._entries: dict[str, CacheEntry] = {}
        self._authorization = authorization
        self._lock = RLock()

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

    def _access(self, operation):
        return require_worker_memory(self._authorization, MemoryScope.run, operation)

    @staticmethod
    def _key(access, key):
        return hashlib.sha256(json.dumps([access.namespace.key(MemoryScope.run), key,
                                         access.policy_digest], separators=(",", ":")).encode()).hexdigest()

    def get(self, *, key: str) -> dict[str, Any] | None:
        try:
            access = self._access(MemoryOperation.read)
            scoped = self._key(access, key)
            with self._lock:
                item = self._entries.get(scoped)
                if not item or time.time() >= item.expires_at or item.policy_digest != access.policy_digest:
                    self._entries.pop(scoped, None)
                    return None
                result = json.loads(json.dumps(item.payload, allow_nan=False))
                if self._access(MemoryOperation.read) != access:
                    return None
                return result
        except (ValueError, TypeError):
            return None

    def put(self, *, key: str, payload: dict[str, Any], quality_score: float) -> None:
        try:
            access = self._access(MemoryOperation.append)
            encoded = json.dumps(payload, allow_nan=False)
            if len(encoded.encode()) > 65536:
                return
            scoped = self._key(access, key)
            with self._lock:
                self._entries = {key: entry for key, entry in self._entries.items()
                                 if time.time() < entry.expires_at}
                if scoped in self._entries:
                    self._access(MemoryOperation.update)
                elif len(self._entries) >= 256:
                    return
                if self._access(MemoryOperation.append) != access:
                    return
                self._entries[scoped] = CacheEntry(
                    scoped, json.loads(encoded), time.time(), float(quality_score),
                    min(time.time() + self._ttl_seconds, access.expires_at), access.policy_digest,
                    access.namespace.key(MemoryScope.run),
                )
        except (ValueError, TypeError):
            return

    def stats(self) -> dict[str, int]:
        try:
            access = self._access(MemoryOperation.read)
            with self._lock:
                count = sum(entry.policy_digest == access.policy_digest and time.time() < entry.expires_at
                            and entry.namespace == access.namespace.key(MemoryScope.run)
                            for entry in self._entries.values())
            if self._access(MemoryOperation.read) != access:
                return {"entry_count": 0}
            return {"entry_count": count}
        except ValueError:
            return {"entry_count": 0}
