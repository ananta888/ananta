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
import time
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
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, *, manifest_hash: str = "") -> dict[str, Any] | None:
        """Return cached payload or ``None`` on miss / stale / invalidated."""
        if not self._enabled:
            return None
        path = self._entry_path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if entry.get("schema") != _CACHE_SCHEMA_VERSION:
            return None
        cached_at = float(entry.get("cached_at") or 0)
        if time.time() - cached_at > self._ttl:
            path.unlink(missing_ok=True)
            return None
        if manifest_hash and entry.get("manifest_hash") != manifest_hash:
            path.unlink(missing_ok=True)
            return None
        return entry.get("payload")

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
        if not allow_sensitive and payload.get("has_sensitive_content"):
            log.debug("pmc_cache: skipping sensitive content for key=%s", key[:16])
            return False
        self._evict_if_needed()
        entry = {
            "schema": _CACHE_SCHEMA_VERSION,
            "key": key,
            "manifest_hash": manifest_hash,
            "cached_at": time.time(),
            "payload": payload,
        }
        try:
            self._entry_path(key).write_text(
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            log.warning("pmc_cache: write failed for key=%s: %s", key[:16], exc)
            return False

    def invalidate(self, key: str) -> None:
        self._entry_path(key).unlink(missing_ok=True)

    def invalidate_all(self) -> int:
        removed = 0
        for p in self._dir.glob("*.json"):
            p.unlink(missing_ok=True)
            removed += 1
        return removed

    def stats(self) -> dict[str, Any]:
        if not self._enabled:
            return {"enabled": False}
        entries = list(self._dir.glob("*.json"))
        return {
            "enabled": True,
            "count": len(entries),
            "max_entries": self._max,
            "ttl_seconds": self._ttl,
            "cache_dir": str(self._dir),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _entry_path(self, key: str) -> Path:
        safe = hashlib.sha256(key.encode()).hexdigest()
        return self._dir / f"{safe}.json"

    def _evict_if_needed(self) -> None:
        entries = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        while len(entries) >= self._max:
            entries.pop(0).unlink(missing_ok=True)


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
