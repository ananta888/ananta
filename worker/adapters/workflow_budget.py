"""Timeout/budget/iterations protection for workflow adapters (LCG-019)."""
from __future__ import annotations

import threading
import time
from typing import Any

from worker.adapters.workflow_adapter_base import WorkerError


class WorkflowBudgetGuard:
    """Raises WorkerError on budget violations; tracks steps, tokens, wall-time."""

    def __init__(
        self,
        *,
        max_steps: int = 12,
        max_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._steps = 0
        self._tokens = 0
        self._start = time.monotonic()
        self._lock = threading.Lock()

    def record_step(self, label: str = "", tokens: int = 0) -> None:
        with self._lock:
            self._steps += 1
            self._tokens += tokens
            steps = self._steps
            total_tokens = self._tokens
            elapsed = time.monotonic() - self._start

        if steps > self._max_steps:
            raise WorkerError(
                "budget_steps_exceeded",
                f"Step budget exceeded ({steps}/{self._max_steps}): {label}",
                {"steps": steps, "max_steps": self._max_steps, "label": label},
            )
        if elapsed > self._timeout_seconds:
            raise WorkerError(
                "budget_timeout",
                f"Timeout exceeded ({elapsed:.1f}s/{self._timeout_seconds}s) at step: {label}",
                {"elapsed_seconds": elapsed, "timeout_seconds": self._timeout_seconds},
            )
        if self._max_tokens and total_tokens > self._max_tokens:
            raise WorkerError(
                "budget_tokens_exceeded",
                f"Token budget exceeded ({total_tokens}/{self._max_tokens})",
                {"tokens": total_tokens, "max_tokens": self._max_tokens},
            )

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "steps": self._steps,
                "tokens": self._tokens,
                "elapsed_seconds": round(time.monotonic() - self._start, 3),
            }
