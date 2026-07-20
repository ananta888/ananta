"""Bounded monotonic poll limiter for semantic relay recipients."""

from __future__ import annotations

import threading
from collections import deque


class SemanticRelayPollLimiter:
    def __init__(self, *, max_per_minute: int, max_scopes: int = 20_000) -> None:
        if max_per_minute <= 0 or max_scopes <= 0:
            raise ValueError("relay_rate_limit_invalid")
        self._max_per_minute = max_per_minute
        self._max_scopes = max_scopes
        self._rows: dict[tuple[str, str, str], deque[float]] = {}
        self._last_seen: dict[tuple[str, str, str], float] = {}
        self._lock = threading.RLock()

    def allow(self, *, tenant_id: str, session_id: str, audience_id: str, now: float) -> bool:
        scope = (tenant_id, session_id, audience_id)
        with self._lock:
            self._expire(now)
            if scope not in self._rows and len(self._rows) >= self._max_scopes:
                oldest = min(self._last_seen, key=lambda key: (self._last_seen[key], key))
                self._rows.pop(oldest, None)
                self._last_seen.pop(oldest, None)
            rows = self._rows.setdefault(scope, deque())
            boundary = now - 60.0
            while rows and rows[0] <= boundary:
                rows.popleft()
            self._last_seen[scope] = now
            if len(rows) >= self._max_per_minute:
                return False
            rows.append(now)
            return True

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "scopes": len(self._rows),
                "events": sum(len(rows) for rows in self._rows.values()),
                "timers": 0,
            }

    def _expire(self, now: float) -> None:
        boundary = now - 60.0
        for scope, rows in list(self._rows.items()):
            while rows and rows[0] <= boundary:
                rows.popleft()
            if not rows and self._last_seen.get(scope, now) <= boundary:
                self._rows.pop(scope, None)
                self._last_seen.pop(scope, None)


__all__ = ["SemanticRelayPollLimiter"]
