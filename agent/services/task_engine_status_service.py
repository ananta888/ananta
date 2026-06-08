"""TaskEngineStatusService (te-012 / te-013).

Thread-safe in-memory store for the current task engine state.
Written by the task execution path when TaskEnginePolicyGate evaluates a task.
Read by the TUI `:te status` command, the Angular status panel, and the AI-Snake
to reflect the current classification in the UI.

The AI-Snake integration (te-012) works by the snake component subscribing to
the status endpoint — no direct coupling to the task loop needed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskEngineStatus:
    active: bool = False
    intent: str | None = None
    task_class: str | None = None          # "deterministic" | "hybrid" | "llm_required"
    llm_required: bool | None = None
    handler_id: str | None = None
    bypassed_llm: bool = False
    reason: str | None = None
    task_id: str | None = None
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskEngineStatusService:
    """Singleton-compatible status store for the task engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = TaskEngineStatus()

    def update(self, decision: Any, *, task_id: str | None = None) -> None:
        """Update status from a GateDecision (or any object with matching attrs)."""
        with self._lock:
            self._status = TaskEngineStatus(
                active=True,
                intent=getattr(decision, "intent", None),
                task_class=getattr(decision, "task_class", None),
                llm_required=getattr(decision, "llm_required", None),
                handler_id=getattr(decision, "handler_id", None),
                bypassed_llm=getattr(decision, "bypass_llm", False),
                reason=getattr(decision, "reason", None),
                task_id=task_id,
                updated_at=time.time(),
            )

    def clear(self) -> None:
        with self._lock:
            self._status = TaskEngineStatus()

    def get(self) -> TaskEngineStatus:
        with self._lock:
            return self._status

    def as_dict(self) -> dict[str, Any]:
        return self.get().as_dict()


# Module-level singleton — safe to import anywhere
_service: TaskEngineStatusService | None = None
_service_lock = threading.Lock()


def get_task_engine_status_service() -> TaskEngineStatusService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TaskEngineStatusService()
    return _service
