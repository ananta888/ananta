"""Focused observation port for Hub-to-Worker forwarding outcomes."""

from __future__ import annotations

from typing import Protocol

from flask import current_app

WORKER_FORWARD_OUTCOME_RECORDER_EXTENSION = "worker_forward_outcome_recorder"


class WorkerForwardOutcomeRecorder(Protocol):
    def record_worker_forward_success(self, worker_url: str) -> None: ...

    def record_worker_forward_failure(
        self,
        worker_url: str,
        reason: str,
        *,
        task_id: str | None = None,
        endpoint: str | None = None,
    ) -> None: ...


def get_worker_forward_outcome_recorder() -> WorkerForwardOutcomeRecorder | None:
    """Resolve the optional control-plane observer without coupling to routes."""

    recorder = current_app.extensions.get(
        WORKER_FORWARD_OUTCOME_RECORDER_EXTENSION
    )
    if recorder is None:
        return None
    if not callable(getattr(recorder, "record_worker_forward_success", None)):
        return None
    if not callable(getattr(recorder, "record_worker_forward_failure", None)):
        return None
    return recorder


__all__ = [
    "WORKER_FORWARD_OUTCOME_RECORDER_EXTENSION",
    "WorkerForwardOutcomeRecorder",
    "get_worker_forward_outcome_recorder",
]
