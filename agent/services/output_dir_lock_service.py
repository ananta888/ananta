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

    @staticmethod
    def canonical_output_dir(path: str) -> str:
        return os.path.realpath(str(path or "")).rstrip("/")

    def acquire(self, *, output_dir: str, owner: str, ttl_seconds: int = 1800) -> tuple[bool, OutputDirLockLease | None, str | None]:
        canonical = self.canonical_output_dir(output_dir)
        now = time.time()
        with self._lock:
            lease = self._leases.get(canonical)
            if lease and lease.expires_at <= now:
                self._leases.pop(canonical, None)
                lease = None
            if lease and lease.owner != owner:
                return False, lease, "output_dir_busy"
            next_lease = OutputDirLockLease(
                lock_id=f"outdir-{abs(hash((canonical, owner, int(now))))}",
                output_dir=canonical,
                owner=owner,
                acquired_at=now,
                expires_at=now + max(60, int(ttl_seconds or 1800)),
            )
            self._leases[canonical] = next_lease
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


_service = OutputDirLockService()


def get_output_dir_lock_service() -> OutputDirLockService:
    return _service

