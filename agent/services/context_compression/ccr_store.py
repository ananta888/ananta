"""
HCCA-005 — Content-addressable Reference Store (CCR)

Stores original content locally by SHA-256 hash.  Each entry is a JSON file
inside *store_path*; a lightweight index file (.index.json) tracks metadata.
No external dependencies — uses the standard library only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_INDEX_FILE = ".index.json"
_DATA_DIR = "data"


@dataclass(frozen=True)
class CCREntry:
    ref: str            # sha256[:24]
    content_type: str
    content_hash: str   # full sha256 hex
    stored_at: float    # unix timestamp
    expires_at: float   # stored_at + ttl_seconds
    byte_size: int
    redacted: bool      # True if secret_redactor ran on it


class CCRStore:
    """Content-addressable local store with TTL expiry."""

    def __init__(
        self,
        store_path: str | Path,
        ttl_hours: int = 72,
        max_bytes_per_item: int = 5_242_880,  # 5 MiB
        redact_secrets: bool = True,
    ) -> None:
        self._store_path = Path(store_path)
        self._ttl_seconds: float = ttl_hours * 3600
        self._max_bytes = max_bytes_per_item
        self._redact_secrets = redact_secrets
        self._data_dir = self._store_path / _DATA_DIR
        self._index_path = self._store_path / _INDEX_FILE

        self._store_path.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(
        self,
        content: str,
        content_type: str = "unknown",
        redacted: bool = False,
    ) -> CCREntry:
        """Persist *content* and return a CCREntry with a stable ref."""
        raw_bytes = content.encode("utf-8")
        if len(raw_bytes) > self._max_bytes:
            raise ValueError(
                f"Content ({len(raw_bytes)} bytes) exceeds max_bytes_per_item "
                f"({self._max_bytes} bytes)."
            )

        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        ref = content_hash[:24]

        now = time.time()
        entry = CCREntry(
            ref=ref,
            content_type=content_type,
            content_hash=content_hash,
            stored_at=now,
            expires_at=now + self._ttl_seconds,
            byte_size=len(raw_bytes),
            redacted=redacted,
        )

        # Write data file
        data_file = self._data_dir / f"{ref}.json"
        data_file.write_text(
            json.dumps({"entry": asdict(entry), "content": content}, ensure_ascii=False),
            encoding="utf-8",
        )

        # Update index
        self._index[ref] = asdict(entry)
        self._persist_index()

        log.debug("CCRStore: stored ref=%s type=%s bytes=%d", ref, content_type, entry.byte_size)
        return entry

    def retrieve(self, ref: str) -> str | None:
        """Return stored content for *ref*, or None if expired/missing."""
        entry_meta = self._index.get(ref)
        if not entry_meta:
            return None
        if time.time() > entry_meta["expires_at"]:
            log.debug("CCRStore: ref=%s expired — returning None", ref)
            return None

        data_file = self._data_dir / f"{ref}.json"
        if not data_file.exists():
            return None

        try:
            payload = json.loads(data_file.read_text(encoding="utf-8"))
            return payload["content"]
        except (json.JSONDecodeError, KeyError) as exc:
            log.warning("CCRStore: failed to read ref=%s: %s", ref, exc)
            return None

    def exists(self, ref: str) -> bool:
        """Return True if *ref* exists and has not expired."""
        entry_meta = self._index.get(ref)
        if not entry_meta:
            return False
        return time.time() <= entry_meta["expires_at"]

    def expire_old(self) -> int:
        """Remove expired entries from disk and index. Returns count removed."""
        now = time.time()
        expired = [ref for ref, meta in self._index.items() if now > meta["expires_at"]]
        for ref in expired:
            data_file = self._data_dir / f"{ref}.json"
            data_file.unlink(missing_ok=True)
            del self._index[ref]
        if expired:
            self._persist_index()
        log.debug("CCRStore: expired %d entries", len(expired))
        return len(expired)

    def diagnostics(self) -> dict[str, Any]:
        """Return a snapshot of store health metrics."""
        now = time.time()
        live_refs = [ref for ref, m in self._index.items() if now <= m["expires_at"]]
        total_bytes = sum(
            self._index[ref]["byte_size"] for ref in live_refs if ref in self._index
        )
        return {
            "store_path": str(self._store_path),
            "live_entries": len(live_refs),
            "total_index_entries": len(self._index),
            "total_live_bytes": total_bytes,
            "ttl_hours": self._ttl_seconds / 3600,
            "max_bytes_per_item": self._max_bytes,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self._index_path.exists():
            return {}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("CCRStore: could not load index — starting fresh: %s", exc)
            return {}

    def _persist_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
