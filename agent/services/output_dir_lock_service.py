from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


@dataclass
class OutputDirLockLease:
    lock_id: str
    output_dir: str
    owner: str
    acquired_at: float
    expires_at: float


class OutputDirLockService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, OutputDirLockLease] = {}
        self._events: list[dict] = []

    def _record_event(self, event: str, *, output_dir: str, owner: str | None, details: dict | None = None) -> None:
        self._events.append(
            {
                "event": str(event),
                "output_dir": str(output_dir),
                "owner": str(owner or ""),
                "timestamp": time.time(),
                "details": dict(details or {}),
            }
        )
        self._events = self._events[-500:]

    @staticmethod
    def canonical_output_dir(path: str) -> str:
        return os.path.realpath(str(path or "")).rstrip("/")

    def acquire(self, *, output_dir: str, owner: str, ttl_seconds: int = 1800) -> tuple[bool, OutputDirLockLease | None, str | None]:
        canonical = self.canonical_output_dir(output_dir)
        now = time.time()
        with self._lock:
            lease = self._leases.get(canonical)
            if lease and lease.expires_at <= now:
                self._record_event(
                    "stale_lock_recovered",
                    output_dir=canonical,
                    owner=lease.owner,
                    details={"expired_by_seconds": round(now - lease.expires_at, 3)},
                )
                self._leases.pop(canonical, None)
                lease = None
            if lease and lease.owner != owner:
                self._record_event("acquire_conflict", output_dir=canonical, owner=owner, details={"held_by": lease.owner})
                return False, lease, "output_dir_busy"
            next_lease = OutputDirLockLease(
                lock_id=f"outdir-{abs(hash((canonical, owner, int(now))))}",
                output_dir=canonical,
                owner=owner,
                acquired_at=now,
                expires_at=now + max(60, int(ttl_seconds or 1800)),
            )
            self._leases[canonical] = next_lease
            self._record_event("acquire_ok", output_dir=canonical, owner=owner, details={"ttl_seconds": max(60, int(ttl_seconds or 1800))})
            return True, next_lease, None

    def release(self, *, output_dir: str, owner: str | None = None) -> None:
        canonical = self.canonical_output_dir(output_dir)
        with self._lock:
            lease = self._leases.get(canonical)
            if not lease:
                return
            if owner and lease.owner != owner:
                return
            self._leases.pop(canonical, None)
            self._record_event("release", output_dir=canonical, owner=lease.owner)

    def recent_events(self) -> list[dict]:
        with self._lock:
            return list(self._events)


_service = OutputDirLockService()


def get_output_dir_lock_service() -> OutputDirLockService:
    return _service
