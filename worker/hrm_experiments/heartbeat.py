"""Worker-owned capability refresh without task or routing authority."""

from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol


class HrmCapabilityPublisher(Protocol):
    def advertise_capability(self, capability: Mapping[str, Any]) -> None: ...


class HrmCapabilityHeartbeat:
    """Refresh a short-lived Hub projection; never creates or delegates work."""

    def __init__(
        self,
        publisher: HrmCapabilityPublisher,
        capability: Mapping[str, Any],
        *,
        interval_seconds: float = 40.0,
    ) -> None:
        self._publisher = publisher
        self._capability = dict(capability)
        self._interval = max(5.0, min(float(interval_seconds), 90.0))
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._last_reason_code: str | None = None

    @property
    def last_reason_code(self) -> str | None:
        with self._state_lock:
            return self._last_reason_code

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="hrm-capability-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def refresh(self) -> None:
        self._publisher.advertise_capability(self._capability)
        with self._state_lock:
            self._last_reason_code = None

    def stop(self) -> None:
        self._stopping.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=min(self._interval + 1.0, 5.0))

    def _run(self) -> None:
        while not self._stopping.wait(self._interval):
            try:
                self.refresh()
            except Exception as exc:
                with self._state_lock:
                    self._last_reason_code = (
                        str(getattr(exc, "reason_code", "") or exc)[:128]
                        or "hrm_capability_refresh_failed"
                    )


__all__ = ["HrmCapabilityHeartbeat", "HrmCapabilityPublisher"]
