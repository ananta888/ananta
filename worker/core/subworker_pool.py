from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from worker.core.subworker_envelope import SubworkerEnvelope


@dataclass(frozen=True)
class SubworkerExecutionResult:
    child_task_id: str
    status: str  # success|queued|denied|failed
    output: Any = None
    reason_code: str = ""


class SubworkerPool:
    """Bounded parallel runner for subworker envelopes.

    Security-relevant checks (capability subset, context ref, depth, max_children) are
    enforced via SubworkerEnvelope.validate() before execution.
    """

    def __init__(self, *, max_children_per_parent: int = 4) -> None:
        self._max_children = max(1, int(max_children_per_parent))
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self._max_children)
        self._active_by_parent: dict[str, int] = {}
        self._lock = threading.Lock()

    def run_subtask(
        self,
        envelope: SubworkerEnvelope,
        *,
        execute_fn: Callable[[SubworkerEnvelope], Any],
        queue_when_full: bool = True,
    ) -> SubworkerExecutionResult:
        errors = envelope.validate()
        if errors:
            return SubworkerExecutionResult(
                child_task_id=envelope.child_task_id,
                status="denied",
                reason_code=";".join(errors),
            )

        if not str(envelope.context_subset_ref or "").strip():
            return SubworkerExecutionResult(
                child_task_id=envelope.child_task_id,
                status="denied",
                reason_code="missing_context_subset_ref",
            )

        with self._lock:
            active = int(self._active_by_parent.get(envelope.parent_execution_id, 0))
            if active >= min(self._max_children, envelope.max_children):
                return SubworkerExecutionResult(
                    child_task_id=envelope.child_task_id,
                    status="queued" if queue_when_full else "denied",
                    reason_code="subworker_fanout_limit_reached",
                )
            self._active_by_parent[envelope.parent_execution_id] = active + 1

        fut = self._executor.submit(execute_fn, envelope)
        try:
            output = fut.result(timeout=max(1.0, float(envelope.timeout_seconds)))
            return SubworkerExecutionResult(
                child_task_id=envelope.child_task_id,
                status="success",
                output=output,
            )
        except Exception as exc:  # noqa: BLE001
            return SubworkerExecutionResult(
                child_task_id=envelope.child_task_id,
                status="failed",
                reason_code=f"subworker_execution_failed:{type(exc).__name__}",
            )
        finally:
            with self._lock:
                current = int(self._active_by_parent.get(envelope.parent_execution_id, 1))
                next_value = max(0, current - 1)
                if next_value == 0:
                    self._active_by_parent.pop(envelope.parent_execution_id, None)
                else:
                    self._active_by_parent[envelope.parent_execution_id] = next_value


__all__ = ["SubworkerPool", "SubworkerExecutionResult"]
