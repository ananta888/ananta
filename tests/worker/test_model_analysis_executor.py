from __future__ import annotations

from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef
from ananta_contracts.model_intelligence_execution import (
    CancellationReason,
    CancellationSignal,
    CompletionOutcome,
    ResourceLease,
)
from worker.model_intelligence.executor import (
    BoundedWorkerResourcePool,
    InMemoryCancellationRegistry,
    ModelAnalysisWorkerExecutor,
)

MODEL_ID = f"model_{'a' * 64}"


def _job() -> AnalysisJob:
    return AnalysisJob(
        job_id="job-001",
        hub_task_id="task-001",
        tenant_id="tenant-001",
        model_id=MODEL_ID,
        analysis_kind="static.tensor-statistics",
        profile_id="profile.static-safe.v1",
        request_sha256="b" * 64,
        requested_artifact_kinds=("tensor.statistics",),
        max_runtime_seconds=60,
        max_output_bytes=4096,
    )


def _lease() -> ResourceLease:
    return ResourceLease(
        lease_id="lease-001",
        job_id="job-001",
        tenant_id="tenant-001",
        worker_id="worker-001",
        lease_generation=1,
        acquired_epoch_ms=1000,
        expires_epoch_ms=61_000,
        max_memory_bytes=1024,
        max_output_bytes=4096,
        completion_key=f"completion_{'c' * 64}",
        request_sha256="b" * 64,
    )


class _Handler:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, job, cancellation):
        self.calls += 1
        cancellation.raise_if_cancelled()
        return (
            ArtifactRef(
                artifact_id="artifact-001",
                job_id=job.job_id,
                kind="tensor.statistics",
                sha256="d" * 64,
                size_bytes=128,
                media_type="application/json",
            ),
        )


def test_executor_reuses_idempotent_completion_and_releases_resources() -> None:
    handler = _Handler()
    cancellations = InMemoryCancellationRegistry()
    resources = BoundedWorkerResourcePool(
        max_active=1,
        max_memory_bytes=2048,
    )
    executor = ModelAnalysisWorkerExecutor(
        handlers={"static.tensor-statistics": handler},
        resources=resources,
        cancellations=cancellations,
        epoch_ms=lambda: 2000,
    )

    first = executor.execute(_job(), _lease())
    second = executor.execute(_job(), _lease())

    assert first is second
    assert first.outcome is CompletionOutcome.SUCCEEDED
    assert handler.calls == 1
    assert resources.snapshot() == {
        "active": 0,
        "reserved_memory_bytes": 0,
    }


def test_executor_observes_fenced_cancellation_before_handler_call() -> None:
    handler = _Handler()
    cancellations = InMemoryCancellationRegistry()
    cancellations.signal(
        CancellationSignal(
            job_id="job-001",
            lease_id="lease-001",
            lease_generation=1,
            reason_code=CancellationReason.HUB_CANCELLED,
            requested_epoch_ms=1500,
        )
    )
    executor = ModelAnalysisWorkerExecutor(
        handlers={"static.tensor-statistics": handler},
        resources=BoundedWorkerResourcePool(
            max_active=1,
            max_memory_bytes=2048,
        ),
        cancellations=cancellations,
        epoch_ms=lambda: 2000,
    )

    completion = executor.execute(_job(), _lease())

    assert completion.outcome is CompletionOutcome.CANCELLED
    assert completion.error is not None
    assert completion.error.reason_code.value == "analysis_cancelled"
    assert handler.calls == 0


def test_executor_redacts_unhandled_exception_details() -> None:
    class _FailingHandler:
        def analyze(self, job, cancellation):
            raise RuntimeError("secret model path and credential")

    executor = ModelAnalysisWorkerExecutor(
        handlers={"static.tensor-statistics": _FailingHandler()},
        resources=BoundedWorkerResourcePool(
            max_active=1,
            max_memory_bytes=2048,
        ),
        cancellations=InMemoryCancellationRegistry(),
        epoch_ms=lambda: 2000,
    )

    completion = executor.execute(_job(), _lease())

    assert completion.outcome is CompletionOutcome.FAILED
    assert completion.error is not None
    assert completion.error.reason_code.value == "internal_error"
    assert "secret" not in str(completion.to_wire())
