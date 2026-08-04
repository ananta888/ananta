"""Worker-local runtime boundary for governed knowledge-index execution.

The Hub owns the budget, while the Worker owns its enforcement.  This module
keeps the monotonic clock and deadline outside the wire contract so request
payloads can never move or recreate the deadline once execution starts.
"""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

KNOWLEDGE_INDEX_WORKER_DEADLINE_EXCEEDED_REASON = (
    "knowledge_index_worker_execution_deadline_exceeded"
)
KNOWLEDGE_INDEX_WORKER_DEADLINE_PORT_REQUIRED_REASON = (
    "knowledge_index_worker_execution_deadline_port_required"
)


class KnowledgeIndexExecutionDeadlineError(TimeoutError):
    """Typed fail-closed execution-boundary error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@runtime_checkable
class KnowledgeIndexExecutionDeadlinePort(Protocol):
    """Narrow cooperative deadline passed only through trusted Worker ports."""

    def checkpoint(self) -> None: ...

    def remaining_seconds(self) -> float: ...


@dataclass(frozen=True, slots=True)
class MonotonicKnowledgeIndexExecutionDeadline:
    expires_at_monotonic: float
    monotonic_clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not math.isfinite(self.expires_at_monotonic):
            raise ValueError("knowledge_index_worker_execution_deadline_invalid")

    def remaining_seconds(self) -> float:
        return max(
            0.0,
            self.expires_at_monotonic - float(self.monotonic_clock()),
        )

    def checkpoint(self) -> None:
        if self.remaining_seconds() <= 0:
            raise KnowledgeIndexExecutionDeadlineError(
                KNOWLEDGE_INDEX_WORKER_DEADLINE_EXCEEDED_REASON
            )


class KnowledgeIndexExecutionGuardPort(Protocol):
    def start(
        self,
        *,
        max_runtime_seconds: int,
    ) -> KnowledgeIndexExecutionDeadlinePort: ...


class MonotonicKnowledgeIndexExecutionGuard:
    """Create one immutable deadline from the already validated Hub budget."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._monotonic_clock = monotonic_clock

    def start(
        self,
        *,
        max_runtime_seconds: int,
    ) -> KnowledgeIndexExecutionDeadlinePort:
        if (
            isinstance(max_runtime_seconds, bool)
            or not isinstance(max_runtime_seconds, int)
            or max_runtime_seconds < 1
        ):
            raise ValueError("knowledge_index_worker_runtime_budget_invalid")
        now = float(self._monotonic_clock())
        return MonotonicKnowledgeIndexExecutionDeadline(
            expires_at_monotonic=now + max_runtime_seconds,
            monotonic_clock=self._monotonic_clock,
        )


class DeadlineAwareKnowledgeIndexExecutionRunner:
    """Fail closed unless a governed execution adapter accepts the deadline.

    The runner intentionally does not use an unbounded background thread: such
    a thread could return a timeout while continuing to mutate Worker storage.
    Concrete adapters must cooperate by checking the supplied deadline around
    and inside long-running loops.
    """

    @staticmethod
    def _accepts_deadline(execute: Callable[..., Any]) -> bool:
        try:
            signature = inspect.signature(execute)
        except (TypeError, ValueError):
            return False
        return bool(
            "execution_deadline" in signature.parameters
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        )

    def execute(
        self,
        execution: Any,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort,
    ) -> Mapping[str, Any]:
        execute = getattr(execution, "execute", None)
        if not callable(execute) or not self._accepts_deadline(execute):
            raise KnowledgeIndexExecutionDeadlineError(
                KNOWLEDGE_INDEX_WORKER_DEADLINE_PORT_REQUIRED_REASON
            )
        execution_deadline.checkpoint()
        result = execute(
            job,
            execution_deadline=execution_deadline,
        )
        execution_deadline.checkpoint()
        return result


__all__ = [
    "DeadlineAwareKnowledgeIndexExecutionRunner",
    "KnowledgeIndexExecutionDeadlineError",
    "KnowledgeIndexExecutionDeadlinePort",
    "KnowledgeIndexExecutionGuardPort",
    "KNOWLEDGE_INDEX_WORKER_DEADLINE_EXCEEDED_REASON",
    "KNOWLEDGE_INDEX_WORKER_DEADLINE_PORT_REQUIRED_REASON",
    "MonotonicKnowledgeIndexExecutionDeadline",
    "MonotonicKnowledgeIndexExecutionGuard",
]
