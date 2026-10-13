"""APMCO-006: ContextPackage cache with TTL and hash-based invalidation.

Cache key: hash of (repo_commit, index_manifest_hash, task_hash,
           working_files_hash, config_hash, mode, surface).

Sensitive content caching can be blocked per policy. The cache is
filesystem-backed (JSON files under ``settings.data_dir / "pmc_cache"``).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = "pmc_cache.v1"
_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_MAX_ENTRIES = 256


class ContextPackageCache:
    """Disk-backed cache for ContextPackage objects.

    All cache entries store the schema version, key components, payload, and
    a ``cached_at`` timestamp. A ``manifest_hash`` mismatch immediately
    invalidates a hit.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        enabled: bool = True,
    ) -> None:
        self._dir = Path(cache_dir) / "pmc_cache"
        self._ttl = max(60, ttl_seconds)
        self._max = max(1, max_entries)
        self._enabled = enabled
        from agent.adapters.namespace_memory_cache import NamespaceMemoryCache
        from agent.services.memory_storage_boundary import MemoryStorageBoundary

        self._storage = NamespaceMemoryCache(self._dir, boundary=MemoryStorageBoundary("hub-context-cache"),
                                              ttl_seconds=min(self._ttl, 86400), max_entries=min(self._max, 4096))

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, *, manifest_hash: str = "") -> dict[str, Any] | None:
        """Return cached payload or ``None`` on miss / stale / invalidated."""
        if not self._enabled:
            return None
        payload = self._storage.get(key, manifest_hash=manifest_hash)
        return payload if isinstance(payload, dict) else None

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        manifest_hash: str = "",
        allow_sensitive: bool = True,
    ) -> bool:
        """Store payload under key. Returns False if caching is skipped."""
        if not self._enabled:
            return False
        # A caller-provided toggle is not an egress/sensitivity approval.
        if payload.get("has_sensitive_content"):
            return False
        return self._storage.put(key, payload, manifest_hash=manifest_hash)

    def invalidate(self, key: str) -> None:
        self._storage.invalidate(key)

    def invalidate_all(self) -> int:
        return self._storage.invalidate()

    def stats(self) -> dict[str, Any]:
        if not self._enabled:
            return {"enabled": False}
        return {"enabled": True, "count": self._storage.count(), "max_entries": self._max,
                "ttl_seconds": self._ttl, "cache_dir": str(self._dir)}


# ── Cache key builder ─────────────────────────────────────────────────────────

def build_cache_key(
    *,
    repo_commit: str = "",
    manifest_hash: str = "",
    task_hash: str = "",
    working_files: list[str] | None = None,
    config_hash: str = "",
    mode: str = "",
    surface: str = "",
) -> str:
    """Build a deterministic cache key from all invalidation-relevant inputs."""
    wf_hash = hashlib.md5(
        "|".join(sorted(working_files or [])).encode(),
        usedforsecurity=False,
    ).hexdigest()
    raw = "|".join([
        str(repo_commit), str(manifest_hash), str(task_hash),
        wf_hash, str(config_hash), str(mode), str(surface),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_task(task_text: str) -> str:
    return hashlib.md5(task_text.strip().encode(), usedforsecurity=False).hexdigest()


def hash_config(config: dict[str, Any] | None) -> str:
    raw = json.dumps(
        (config or {}).get("pre_model_context") or {},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()
