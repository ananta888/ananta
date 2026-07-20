"""Single-task executor with no orchestration or peer-network dependencies."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from ananta_contracts.semantic_compute import SemanticComputeWorkerResult, SemanticComputeWorkerTask


class SemanticComputeWorkerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class WorkerArtifact:
    content: bytes
    metrics: Mapping[str, float]


class SemanticExecutorPort(Protocol):
    def execute(self, task: SemanticComputeWorkerTask, cancelled: Callable[[], bool]) -> WorkerArtifact: ...


class ArtifactPublisherPort(Protocol):
    def publish(self, task: SemanticComputeWorkerTask, content: bytes) -> str: ...


class LeaseGuardPort(Protocol):
    def authorized(self, task: SemanticComputeWorkerTask) -> bool: ...


class SemanticComputeWorkerHandler:
    """Executes exactly the Hub envelope supplied; it cannot spawn work."""

    def __init__(
        self,
        *,
        executor: SemanticExecutorPort,
        publisher: ArtifactPublisherPort,
        lease_guard: LeaseGuardPort,
        clock_ms: Callable[[], int] | None = None,
        cancelled: Callable[[str], bool] | None = None,
    ) -> None:
        self._executor = executor
        self._publisher = publisher
        self._lease_guard = lease_guard
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._cancelled = cancelled or (lambda _task_id: False)

    def handle(self, raw: object) -> dict:
        task = SemanticComputeWorkerTask.from_dict(raw)
        self._assert_authorized(task)

        def stopped() -> bool:
            return (
                self._cancelled(task.task_id)
                or self._clock_ms() >= task.deadline_epoch_ms
                or not self._lease_guard.authorized(task)
            )

        artifact = self._executor.execute(task, stopped)
        if stopped():
            raise SemanticComputeWorkerError("execution_authority_lost")
        if (
            not isinstance(artifact.content, bytes)
            or len(artifact.content) > int(task.resource_budget["artifact_bytes"])
        ):
            raise SemanticComputeWorkerError("artifact_budget_exceeded")
        # The Hub-selected publisher target receives all fencing bindings.
        artifact_ref = self._publisher.publish(task, artifact.content)
        if stopped():
            raise SemanticComputeWorkerError("late_artifact_rejected")
        result = SemanticComputeWorkerResult(
            task_id=task.task_id,
            contract_id=task.contract_id,
            contract_digest=task.contract_digest,
            lease_id=task.lease_id,
            fencing_token=task.fencing_token,
            session_id=task.session_id,
            epoch=task.epoch,
            task_type=task.task_type,
            audience=task.audience,
            status="completed",
            result_digest=hashlib.sha256(artifact.content).hexdigest(),
            artifact_refs=(artifact_ref,),
            completed_at_ms=self._clock_ms(),
            metrics=dict(artifact.metrics),
        )
        return result.to_dict()

    def _assert_authorized(self, task: SemanticComputeWorkerTask) -> None:
        if self._cancelled(task.task_id):
            raise SemanticComputeWorkerError("task_cancelled")
        if self._clock_ms() >= task.deadline_epoch_ms:
            raise SemanticComputeWorkerError("deadline_expired")
        if not self._lease_guard.authorized(task):
            raise SemanticComputeWorkerError("lease_not_authorized")


__all__ = [
    "ArtifactPublisherPort", "LeaseGuardPort", "SemanticComputeWorkerError",
    "SemanticComputeWorkerHandler", "SemanticExecutorPort", "WorkerArtifact",
]
