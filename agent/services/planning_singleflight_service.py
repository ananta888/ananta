from __future__ import annotations

import threading
import time


class PlanningSingleFlightService:
    """Per-goal in-memory lease guard to prevent duplicate concurrent planning runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, float] = {}

    def acquire(self, *, goal_id: str, ttl_seconds: int = 900) -> bool:
        gid = str(goal_id or "").strip()
        if not gid:
            return False
        now = time.time()
        ttl = max(30, int(ttl_seconds))
        with self._lock:
            expires = float(self._leases.get(gid, 0.0) or 0.0)
            if expires > now:
                return False
            self._leases[gid] = now + ttl
            return True

    def release(self, *, goal_id: str) -> None:
        gid = str(goal_id or "").strip()
        if not gid:
            return
        with self._lock:
            self._leases.pop(gid, None)

    def is_active(self, *, goal_id: str) -> bool:
        gid = str(goal_id or "").strip()
        if not gid:
            return False
        now = time.time()
        with self._lock:
            expires = float(self._leases.get(gid, 0.0) or 0.0)
            if expires <= now:
                self._leases.pop(gid, None)
                return False
            return True


_SERVICE = PlanningSingleFlightService()


def get_planning_singleflight_service() -> PlanningSingleFlightService:
    return _SERVICE
