from __future__ import annotations

"""Concurrency control for model requests.

The :class:`ModelPool` coordinates access to LLM models so that only a
configured number of concurrent requests per (provider, model) pair are
executed. Additional requests are queued in a thread- and asyncio-safe
manner and resumed when a slot becomes free.
"""

from collections import deque
from dataclasses import dataclass, field
import asyncio
import threading
from typing import Deque, Dict, Tuple


Key = Tuple[str, str]


@dataclass
class _QueueEntry:
    """Bookkeeping for a single model."""

    limit: int
    in_use: int = 0
    waiters: Deque[asyncio.Future] = field(default_factory=deque)


class ModelPool:
    """Simple pool limiting parallel LLM requests.

    The pool keeps track of active requests per model and queues additional
    callers. It is safe to use from multiple asyncio tasks or threads.
    """

    def __init__(self) -> None:
        self._pools: Dict[Key, _QueueEntry] = {}
        self._lock = threading.Lock()

    def register(self, provider: str, model: str, limit: int = 1) -> None:
        """Register a ``(provider, model)`` pair with an optional limit.

        Parameters
        ----------
        provider, model:
            Identifier for the LLM provider and the specific model.
        limit:
            Maximum number of concurrent requests allowed for the model.
        """

        key = (provider, model)
        with self._lock:
            if key not in self._pools:
                self._pools[key] = _QueueEntry(limit=limit)

    async def acquire(self, provider: str, model: str) -> None:
        """Wait until a slot for ``(provider, model)`` becomes available."""

        key = (provider, model)
        while True:
            with self._lock:
                entry = self._pools.get(key)
                if entry is None:
                    # Auto-register unknown models with default limit 1
                    entry = _QueueEntry(limit=1)
                    self._pools[key] = entry
                if entry.in_use < entry.limit:
                    entry.in_use += 1
                    return
                fut = asyncio.get_running_loop().create_future()
                entry.waiters.append(fut)
            await fut
            # A slot has been reserved for us; simply return.
            return

    def release(self, provider: str, model: str) -> None:
        """Release a slot previously acquired."""

        key = (provider, model)
        with self._lock:
            entry = self._pools.get(key)
            if entry is None:
                raise KeyError(f"Model {provider}/{model} not registered")
            if entry.waiters:
                fut = entry.waiters.popleft()
                # Transfer slot directly to waiting task without
                # adjusting ``in_use``.
            else:
                entry.in_use -= 1
                return
        # Wake the next waiter outside the lock to avoid deadlocks
        fut.set_result(True)
