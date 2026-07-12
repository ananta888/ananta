"""Tenant-, policy- and revision-bound cache for restricted inference results."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class RestrictedInferenceCacheKey:
    tenant_id: str
    operation: str
    manifest_digest: str
    policy_hash: str
    config_hash: str
    input_hash: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        operation: str,
        manifest_digest: str,
        policy_hash: str,
        config: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
    ) -> "RestrictedInferenceCacheKey":
        return cls(
            tenant_id=tenant_id,
            operation=operation,
            manifest_digest=manifest_digest,
            policy_hash=policy_hash,
            config_hash=_digest_json(dict(config or {})),
            input_hash=_digest_json(dict(payload)),
        )

    def digest(self) -> str:
        return _digest_json(
            {
                "tenant_id": self.tenant_id,
                "operation": self.operation,
                "manifest_digest": self.manifest_digest,
                "policy_hash": self.policy_hash,
                "config_hash": self.config_hash,
                "input_hash": self.input_hash,
            }
        )


@dataclass(frozen=True)
class _CacheEntry:
    payload: bytes
    integrity_digest: str
    expires_at_ns: int


class RestrictedInferenceCache:
    """Small deterministic in-memory LRU; raw inputs never become keys."""

    def __init__(
        self,
        *,
        max_entries: int = 0,
        ttl_seconds: float = 300.0,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        if max_entries < 0:
            raise ValueError("max_entries must be non-negative")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max_entries = max_entries
        self._ttl_ns = int(ttl_seconds * 1_000_000_000)
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._max_entries > 0

    def get(self, key: RestrictedInferenceCacheKey) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        digest = key.digest()
        now = self._monotonic_ns()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            if entry.expires_at_ns <= now or hashlib.sha256(entry.payload).hexdigest() != entry.integrity_digest:
                self._entries.pop(digest, None)
                return None
            try:
                value = json.loads(entry.payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._entries.pop(digest, None)
                return None
            if not isinstance(value, dict):
                self._entries.pop(digest, None)
                return None
            self._entries.move_to_end(digest)
            return value

    def put(self, key: RestrictedInferenceCacheKey, result: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        payload = json.dumps(dict(result), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        digest = key.digest()
        with self._lock:
            self._entries[digest] = _CacheEntry(
                payload=payload,
                integrity_digest=hashlib.sha256(payload).hexdigest(),
                expires_at_ns=self._monotonic_ns() + self._ttl_ns,
            )
            self._entries.move_to_end(digest)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _digest_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
