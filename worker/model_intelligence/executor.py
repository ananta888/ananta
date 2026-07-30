"""Single-attempt model-analysis execution for isolated workers."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator, Mapping, Protocol, Sequence

from ananta_contracts.model_intelligence import (
    AnalysisJob,
    ArtifactRef,
    ModelIntelligenceReasonCode,
    build_error_envelope,
)
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CancellationSignal,
    CompletionOutcome,
    ResourceLease,
)


class ModelAnalysisExecutionError(RuntimeError):
    def __init__(
        self,
        reason_code: ModelIntelligenceReasonCode,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(reason_code.value)


class ModelAnalysisCancelled(ModelAnalysisExecutionError):
    def __init__(self, *, job_id: str) -> None:
        super().__init__(
            ModelIntelligenceReasonCode.ANALYSIS_CANCELLED,
            details={"job_id": job_id},
        )


class AnalysisHandler(Protocol):
    def analyze(
        self,
        job: AnalysisJob,
        cancellation: "ExecutionCancellationToken",
    ) -> Sequence[ArtifactRef]: ...


class WorkerResourcePort(Protocol):
    def hold(self, lease: ResourceLease) -> ContextManager[None]: ...


class WorkerCancellationPort(Protocol):
    def cancellation_for(
        self,
        *,
        job_id: str,
        lease_id: str,
        lease_generation: int,
    ) -> CancellationSignal | None: ...


class CompletionJournal(Protocol):
    def get(self, completion_key: str) -> AnalysisCompletion | None: ...

    def put_if_absent(
        self,
        completion: AnalysisCompletion,
    ) -> AnalysisCompletion: ...


class InMemoryCompletionJournal:
    """Thread-safe worker cache; the Hub remains the durable completion owner."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._completions: dict[str, AnalysisCompletion] = {}

    def get(self, completion_key: str) -> AnalysisCompletion | None:
        with self._lock:
            return self._completions.get(completion_key)

    def put_if_absent(
        self,
        completion: AnalysisCompletion,
    ) -> AnalysisCompletion:
        with self._lock:
            existing = self._completions.get(completion.completion_key)
            if existing is not None:
                if (
                    existing.job_id != completion.job_id
                    or existing.lease_id != completion.lease_id
                    or existing.lease_generation != completion.lease_generation
                ):
                    raise ModelAnalysisExecutionError(
                        ModelIntelligenceReasonCode.CONTRACT_INVALID,
                        details={"job_id": completion.job_id},
                    )
                return existing
            self._completions[completion.completion_key] = completion
            return completion


class InMemoryCancellationRegistry:
    """Idempotent cancellation observation scoped to one fenced lease."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._signals: dict[tuple[str, str, int], CancellationSignal] = {}

    def signal(self, signal: CancellationSignal) -> bool:
        key = (signal.job_id, signal.lease_id, signal.lease_generation)
        with self._lock:
            existing = self._signals.get(key)
            if existing is not None:
                if existing != signal:
                    raise ModelAnalysisExecutionError(
                        ModelIntelligenceReasonCode.CONTRACT_INVALID,
                        details={"job_id": signal.job_id},
                    )
                return False
            self._signals[key] = signal
            return True

    def cancellation_for(
        self,
        *,
        job_id: str,
        lease_id: str,
        lease_generation: int,
    ) -> CancellationSignal | None:
        with self._lock:
            return self._signals.get((job_id, lease_id, lease_generation))


class BoundedWorkerResourcePool:
    """Non-queuing local guard beneath the Hub-owned global queue."""

    def __init__(
        self,
        *,
        max_active: int,
        max_memory_bytes: int,
    ) -> None:
        if max_active < 1 or max_memory_bytes < 1:
            raise ValueError("model_analysis_worker_resource_config_invalid")
        self._max_active = max_active
        self._max_memory_bytes = max_memory_bytes
        self._lock = threading.RLock()
        self._active: dict[str, int] = {}

    @contextmanager
    def hold(self, lease: ResourceLease) -> Iterator[None]:
        with self._lock:
            if lease.lease_id in self._active:
                raise ModelAnalysisExecutionError(
                    ModelIntelligenceReasonCode.CONTRACT_INVALID,
                    details={"job_id": lease.job_id},
                )
            reserved = sum(self._active.values())
            if (
                len(self._active) >= self._max_active
                or reserved + lease.max_memory_bytes > self._max_memory_bytes
            ):
                raise ModelAnalysisExecutionError(
                    ModelIntelligenceReasonCode.RUNTIME_UNAVAILABLE,
                    details={
                        "job_id": lease.job_id,
                        "limit_name": "worker_resource_pool",
                    },
                )
            self._active[lease.lease_id] = lease.max_memory_bytes
        try:
            yield
        finally:
            with self._lock:
                self._active.pop(lease.lease_id, None)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "active": len(self._active),
                "reserved_memory_bytes": sum(self._active.values()),
            }


class ExecutionCancellationToken:
    def __init__(
        self,
        *,
        lease: ResourceLease,
        cancellations: WorkerCancellationPort,
        epoch_ms: Callable[[], int],
    ) -> None:
        self._lease = lease
        self._cancellations = cancellations
        self._epoch_ms = epoch_ms

    def raise_if_cancelled(self) -> None:
        if self._epoch_ms() >= self._lease.expires_epoch_ms:
            raise ModelAnalysisExecutionError(
                ModelIntelligenceReasonCode.ANALYSIS_DEADLINE_EXCEEDED,
                details={"job_id": self._lease.job_id},
            )
        signal = self._cancellations.cancellation_for(
            job_id=self._lease.job_id,
            lease_id=self._lease.lease_id,
            lease_generation=self._lease.lease_generation,
        )
        if signal is not None:
            raise ModelAnalysisCancelled(job_id=self._lease.job_id)


class ModelAnalysisWorkerExecutor:
    """Executes one Hub-issued job without creating or delegating work."""

    def __init__(
        self,
        *,
        handlers: Mapping[str, AnalysisHandler],
        resources: WorkerResourcePort,
        cancellations: WorkerCancellationPort,
        completions: CompletionJournal | None = None,
        epoch_ms: Callable[[], int] | None = None,
    ) -> None:
        if not handlers:
            raise ValueError("model_analysis_handlers_required")
        self._handlers = dict(handlers)
        self._resources = resources
        self._cancellations = cancellations
        self._completions = completions or InMemoryCompletionJournal()
        self._epoch_ms = epoch_ms or (lambda: time.time_ns() // 1_000_000)

    def execute(
        self,
        job: AnalysisJob,
        lease: ResourceLease,
    ) -> AnalysisCompletion:
        self._validate_binding(job, lease)
        existing = self._completions.get(lease.completion_key)
        if existing is not None:
            if (
                existing.job_id != job.job_id
                or existing.lease_id != lease.lease_id
                or existing.lease_generation != lease.lease_generation
            ):
                raise ModelAnalysisExecutionError(
                    ModelIntelligenceReasonCode.CONTRACT_INVALID,
                    details={"job_id": job.job_id},
                )
            return existing

        token = ExecutionCancellationToken(
            lease=lease,
            cancellations=self._cancellations,
            epoch_ms=self._epoch_ms,
        )
        try:
            token.raise_if_cancelled()
            handler = self._handlers.get(job.analysis_kind)
            if handler is None:
                raise ModelAnalysisExecutionError(
                    ModelIntelligenceReasonCode.CAPABILITY_UNSUPPORTED,
                    details={
                        "analysis_kind": job.analysis_kind,
                        "job_id": job.job_id,
                    },
                )
            with self._resources.hold(lease):
                artifacts = tuple(handler.analyze(job, token))
                token.raise_if_cancelled()
                self._validate_artifacts(job, lease, artifacts)
            completion = AnalysisCompletion(
                job_id=job.job_id,
                lease_id=lease.lease_id,
                lease_generation=lease.lease_generation,
                completion_key=lease.completion_key,
                outcome=CompletionOutcome.SUCCEEDED,
                artifacts=artifacts,
            )
        except ModelAnalysisCancelled as exc:
            completion = self._failure_completion(
                lease,
                outcome=CompletionOutcome.CANCELLED,
                error=exc,
            )
        except ModelAnalysisExecutionError as exc:
            completion = self._failure_completion(
                lease,
                outcome=CompletionOutcome.FAILED,
                error=exc,
            )
        except Exception:
            completion = self._failure_completion(
                lease,
                outcome=CompletionOutcome.FAILED,
                error=ModelAnalysisExecutionError(
                    ModelIntelligenceReasonCode.INTERNAL_ERROR,
                    details={"job_id": job.job_id, "operation": "analyze"},
                ),
            )
        return self._completions.put_if_absent(completion)

    def _failure_completion(
        self,
        lease: ResourceLease,
        *,
        outcome: CompletionOutcome,
        error: ModelAnalysisExecutionError,
    ) -> AnalysisCompletion:
        return AnalysisCompletion(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            lease_generation=lease.lease_generation,
            completion_key=lease.completion_key,
            outcome=outcome,
            error=build_error_envelope(
                error.reason_code,
                details=error.details,
            ),
        )

    def _validate_binding(
        self,
        job: AnalysisJob,
        lease: ResourceLease,
    ) -> None:
        if (
            lease.job_id != job.job_id
            or lease.tenant_id != job.tenant_id
            or lease.request_sha256 != job.request_sha256
            or lease.max_output_bytes > job.max_output_bytes
            or self._epoch_ms() >= lease.expires_epoch_ms
        ):
            raise ModelAnalysisExecutionError(
                ModelIntelligenceReasonCode.CONTRACT_INVALID,
                details={"job_id": job.job_id},
            )

    @staticmethod
    def _validate_artifacts(
        job: AnalysisJob,
        lease: ResourceLease,
        artifacts: Sequence[ArtifactRef],
    ) -> None:
        if (
            not artifacts
            or len(artifacts) > 64
            or any(artifact.job_id != job.job_id for artifact in artifacts)
            or sum(artifact.size_bytes for artifact in artifacts)
            > min(job.max_output_bytes, lease.max_output_bytes)
        ):
            raise ModelAnalysisExecutionError(
                ModelIntelligenceReasonCode.RESOURCE_LIMIT_EXCEEDED,
                details={
                    "job_id": job.job_id,
                    "limit_name": "analysis_artifacts",
                },
            )


__all__ = [
    "AnalysisHandler",
    "BoundedWorkerResourcePool",
    "CompletionJournal",
    "ExecutionCancellationToken",
    "InMemoryCancellationRegistry",
    "InMemoryCompletionJournal",
    "ModelAnalysisCancelled",
    "ModelAnalysisExecutionError",
    "ModelAnalysisWorkerExecutor",
    "WorkerCancellationPort",
    "WorkerResourcePort",
]
