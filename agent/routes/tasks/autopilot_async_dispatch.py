"""Non-blocking autopilot dispatch: submit without waiting, reap on later ticks.

The default dispatch is synchronous: a tick waits for its dispatches, so one
slow propose freezes the whole loop until the deadline. With
``autopilot.async_dispatch_enabled`` a tick only submits up to the free
concurrency slots, returns immediately, and reaps finished dispatches on the
next tick. Workers keep running in the background; their task status is updated
by the dispatch call itself.
"""
from __future__ import annotations

import concurrent.futures
import threading
from typing import Any, Callable


def async_dispatch_enabled(app_config: dict | None) -> bool:
    return bool(((app_config or {}).get("autopilot") or {}).get("async_dispatch_enabled"))


class AsyncDispatchState:
    def __init__(self, *, max_workers: int = 4) -> None:
        self._lock = threading.Lock()
        self._futures: dict[concurrent.futures.Future, str] = {}
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._max_workers = max(1, int(max_workers))

    def _executor_or_create(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers)
        return self._executor

    def inflight(self) -> int:
        with self._lock:
            return len(self._futures)

    def submit(self, fn: Callable[..., Any], *, task_id: str, **kwargs: Any) -> concurrent.futures.Future:
        future = self._executor_or_create().submit(fn, **kwargs)
        with self._lock:
            self._futures[future] = str(task_id)
        return future

    def reap(self) -> list[tuple[str, Any]]:
        """``(task_id, result_or_exception)`` for every finished dispatch."""
        with self._lock:
            finished = [(self._futures.pop(future), future) for future in list(self._futures) if future.done()]
        results: list[tuple[str, Any]] = []
        for task_id, future in finished:
            try:
                results.append((task_id, future.result()))
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a failed result
                results.append((task_id, exc))
        return results

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False)
