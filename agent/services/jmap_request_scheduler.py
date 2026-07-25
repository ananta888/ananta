from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Protocol, TypeVar

from agent.services.mail_provider_ports import MailProviderResult


T = TypeVar("T")


class JmapCancellationSignal(Protocol):
    def is_cancelled(self) -> bool:
        ...

    def wait(self, timeout: float) -> bool:
        ...


class JmapCancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(max(0.0, float(timeout)))


@dataclass(frozen=True, slots=True)
class JmapSchedulerSnapshot:
    active: int
    queued: int
    maximum_concurrent: int
    maximum_queued: int


class JmapRequestScheduler(Generic[T]):
    """Per-session bounded scheduler with explicit backpressure and cancellation."""

    def __init__(
        self,
        *,
        maximum_concurrent_requests: int,
        maximum_queued_requests: int,
        queue_timeout_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maximum_concurrent = max(1, int(maximum_concurrent_requests))
        self._maximum_queued = max(0, int(maximum_queued_requests))
        self._queue_timeout_seconds = max(0.01, float(queue_timeout_seconds))
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._active = 0
        self._queued = 0

    def execute(
        self,
        callback: Callable[[], MailProviderResult[T]],
        *,
        cancellation: JmapCancellationSignal | None = None,
    ) -> MailProviderResult[T]:
        denied = self._acquire(cancellation)
        if denied is not None:
            return denied
        try:
            if cancellation is not None and cancellation.is_cancelled():
                return MailProviderResult(ok=False, reason_code="jmap_request_cancelled")
            return callback()
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def snapshot(self) -> JmapSchedulerSnapshot:
        with self._condition:
            return JmapSchedulerSnapshot(
                active=self._active,
                queued=self._queued,
                maximum_concurrent=self._maximum_concurrent,
                maximum_queued=self._maximum_queued,
            )

    def _acquire(
        self,
        cancellation: JmapCancellationSignal | None,
    ) -> MailProviderResult[T] | None:
        with self._condition:
            if cancellation is not None and cancellation.is_cancelled():
                return MailProviderResult(ok=False, reason_code="jmap_request_cancelled")
            if self._active < self._maximum_concurrent:
                self._active += 1
                return None
            if self._queued >= self._maximum_queued:
                return MailProviderResult(
                    ok=False,
                    reason_code="jmap_request_queue_full",
                    retryable=True,
                )
            self._queued += 1
            deadline = self._monotonic() + self._queue_timeout_seconds
            try:
                while True:
                    if cancellation is not None and cancellation.is_cancelled():
                        return MailProviderResult(ok=False, reason_code="jmap_request_cancelled")
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        return MailProviderResult(
                            ok=False,
                            reason_code="jmap_request_queue_timeout",
                            retryable=True,
                        )
                    if self._active < self._maximum_concurrent:
                        self._active += 1
                        return None
                    self._condition.wait(timeout=min(0.05, remaining))
            finally:
                self._queued -= 1


__all__ = [
    "JmapCancellationSignal",
    "JmapCancellationToken",
    "JmapRequestScheduler",
    "JmapSchedulerSnapshot",
]
