from __future__ import annotations

import threading
import time

from .errors import BackendCancelledError, BackendTimeoutError


class BackendCancellationToken:
    """Small cooperative cancellation/deadline port for local adapters."""

    def __init__(self, *, deadline_monotonic: float) -> None:
        self.deadline_monotonic = float(deadline_monotonic)
        self._event = threading.Event()
        self._reason_code = "cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or time.monotonic() >= self.deadline_monotonic

    @property
    def reason_code(self) -> str:
        return "timeout" if time.monotonic() >= self.deadline_monotonic else self._reason_code

    def cancel(self, reason_code: str = "cancelled") -> None:
        self._reason_code = "timeout" if reason_code == "timeout" else "cancelled"
        self._event.set()

    def remaining_seconds(self, *, maximum: float | None = None) -> float:
        remaining = max(0.0, self.deadline_monotonic - time.monotonic())
        return min(remaining, maximum) if maximum is not None else remaining

    def raise_if_cancelled(self) -> None:
        if not self.cancelled:
            return
        if self.reason_code == "timeout":
            raise BackendTimeoutError("voice backend deadline exceeded")
        raise BackendCancelledError("voice backend was cancelled")
