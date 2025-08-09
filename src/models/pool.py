"""Light‑weight model pool used to throttle concurrent requests."""

from contextlib import contextmanager
import threading
from typing import Dict, Tuple


class ModelPool:
    def __init__(self) -> None:
        self._semaphores: Dict[Tuple[str, str], threading.BoundedSemaphore] = {}
        self._limits: Dict[Tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def register(self, provider: str, model: str, limit: int) -> None:
        with self._lock:
            key = (provider, model)
            self._limits[key] = limit
            self._semaphores[key] = threading.BoundedSemaphore(limit)

    @contextmanager
    def acquire(self, provider: str, model: str):
        key = (provider, model)
        if key not in self._semaphores:
            raise KeyError(f"{provider}:{model} not registered")
        sem = self._semaphores[key]
        sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def status(self) -> Dict[str, int]:
        """Return remaining slots per provider/model pair."""
        with self._lock:
            return {
                f"{provider}:{model}": sem._value  # type: ignore[attr-defined]
                for (provider, model), sem in self._semaphores.items()
            }
