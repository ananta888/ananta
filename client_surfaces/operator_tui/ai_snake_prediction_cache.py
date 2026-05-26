from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: dict[str, Any]
    expires_at: float


class PredictionCache:
    def __init__(self, *, ttl_seconds: int = 30) -> None:
        self.ttl_seconds = max(15, min(60, int(ttl_seconds)))
        self._entries: dict[str, CacheEntry] = {}

    def make_key(
        self,
        *,
        section: str,
        target_ref: str,
        intent_kind: str,
        context_hash: str,
    ) -> str:
        payload = {
            "section": _normalize(section),
            "target_ref": _normalize(target_ref),
            "intent_kind": _normalize(intent_kind),
            "context_hash": _normalize(context_hash),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def get(self, key: str, *, now: float | None = None) -> dict[str, Any] | None:
        ts = time.time() if now is None else float(now)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= ts:
            self._entries.pop(key, None)
            return None
        return dict(entry.value)

    def set(self, key: str, value: dict[str, Any], *, now: float | None = None, ttl_seconds: int | None = None) -> None:
        ts = time.time() if now is None else float(now)
        ttl = self.ttl_seconds if ttl_seconds is None else max(5, int(ttl_seconds))
        self._entries[key] = CacheEntry(value=dict(value), expires_at=ts + ttl)

    def clear(self) -> None:
        self._entries.clear()

    def prune(self, *, now: float | None = None) -> int:
        ts = time.time() if now is None else float(now)
        before = len(self._entries)
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= ts:
                self._entries.pop(key, None)
        return before - len(self._entries)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())[:120]
