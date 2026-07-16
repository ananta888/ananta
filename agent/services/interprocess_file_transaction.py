"""Small cross-process transaction boundary for replace-based JSON stores.

Atomic file replacement prevents torn reads, but it does not prevent two Hub
processes from loading the same generation and then overwriting each other's
changes.  This re-entrant lock keeps the complete read-modify-write cycle
exclusive while preserving the existing replace-based durability strategy.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import TracebackType

import portalocker


class InterProcessFileTransaction:
    """Re-entrant process lock backed by one stable sidecar lock file."""

    def __init__(self, lock_path: str | Path, *, timeout_seconds: float = 30.0) -> None:
        self._path = Path(lock_path)
        self._timeout_seconds = timeout_seconds
        self._state = threading.local()

    def __enter__(self) -> "InterProcessFileTransaction":
        depth = int(getattr(self._state, "depth", 0))
        if depth == 0:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            lock = portalocker.Lock(
                str(self._path),
                mode="a+",
                timeout=self._timeout_seconds,
            )
            lock.acquire()
            os.chmod(self._path, 0o600)
            self._state.lock = lock
        self._state.depth = depth + 1
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        depth = int(getattr(self._state, "depth", 0))
        if depth <= 1:
            lock = getattr(self._state, "lock", None)
            try:
                if lock is not None:
                    lock.release()
            finally:
                self._state.depth = 0
                self._state.lock = None
        else:
            self._state.depth = depth - 1
        return False


__all__ = ["InterProcessFileTransaction"]
