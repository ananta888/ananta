from __future__ import annotations

import pytest

from agent.services.model_analysis_job_service import (
    InMemoryModelAnalysisJobRepository,
    ModelAnalysisJobService,
    ModelAnalysisJobServiceError,
    ModelAnalysisJobState,
    ModelAnalysisLimits,
)
from agent.services.model_analysis_task_port import (
    HubModelAnalysisTaskSubmissionPort,
    ModelAnalysisTaskReference,
)
from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CompletionOutcome,
)

MODEL_ID = f"model_{'a' * 64}"


def _job(number: int = 1, *, tenant: str = "tenant-001") -> AnalysisJob:
    return AnalysisJob(
        job_id=f"job-{number:03d}",
        hub_task_id=f"task-{number:03d}",
        tenant_id=tenant,
        model_id=MODEL_ID,
        analysis_kind="static.tensor-statistics",
        profile_id="profile.static-safe.v1",
        request_sha256=f"{number:064x}",
        requested_artifact_kinds=("tensor.statistics",),
        max_runtime_seconds=60,
        max_output_bytes=4096,
    )


class _Tasks:
    def __init__(self, *, fail_submissions: int = 0) -> None:
        self.fail_submissions = fail_submissions
        self.events = []

    def submit(self, job):
        self.events.append(("submit", job.job_id))
        if self.fail_submissions:
            self.fail_submissions -= 1
            raise RuntimeError("queue unavailable")
        return ModelAnalysisTaskReference(
            job.hub_task_id,
            f"execution-{job.job_id}",
            "assigned",
        )

    def mark_running(self, job, *, worker_id):
        self.events.append(("running", job.job_id, worker_id))

    def mark_cancel_requested(self, job, *, reason_code):
        self.events.append(("cancel", job.job_id, reason_code))

    def finish(self, job, *, status, reason_code):
        self.events.append(("finish", job.job_id, status, reason_code))


def _service(tasks=None, *, clock=None, limits=None):
    return ModelAnalysisJobService(
        repository=InMemoryModelAnalysisJobRepository(),
        tasks=tasks or _Tasks(),
        limits=limits or ModelAnalysisLimits(),
        epoch_ms=clock or (lambda: 1000),
    )


def test_submit_is_tenant_bound_idempotent_and_detects_payload_conflict() -> None:
    service = _service()
    first = service.submit(_job(), idempotency_key="request-key-001")
    replay = service.submit(_job(), idempotency_key="request-key-001")

    assert first == replay
    assert first.state is ModelAnalysisJobState.QUEUED

    with pytest.raises(ModelAnalysisJobServiceError) as raised:
        service.submit(_job(2), idempotency_key="request-key-001")
    assert raised.value.reason_code == "model_analysis_idempotency_conflict"


def test_queue_and_tenant_limits_are_enforced_atomically() -> None:
    service = _service(
        limits=ModelAnalysisLimits(
            max_global_queued=2,
            max_tenant_queued=1,
            max_tenant_active=2,
        )
    )
    service.submit(_job(1), idempotency_key="request-key-001")

    with pytest.raises(ModelAnalysisJobServiceError) as raised:
        service.submit(_job(2), idempotency_key="request-key-002")
    assert raised.value.reason_code == "model_analysis_tenant_queue_full"

    service.submit(
        _job(3, tenant="tenant-002"),
        idempotency_key="request-key-003",
    )
    with pytest.raises(ModelAnalysisJobServiceError) as raised:
        service.submit(
            _job(4, tenant="tenant-003"),
            idempotency_key="request-key-004",
        )
    assert raised.value.reason_code == "model_analysis_global_queue_full"


def test_claim_and_completion_are_fenced_and_completion_is_idempotent() -> None:
    tasks = _Tasks()
    service = _service(tasks)
    queued = service.submit(_job(), idempotency_key="request-key-001")
    running, lease = service.claim(
        tenant_id="tenant-001",
        job_id="job-001",
        worker_id="worker-001",
        expected_version=queued.version,
        lease_seconds=30,
        max_memory_bytes=2048,
    )
    artifact = ArtifactRef(
        artifact_id="artifact-001",
        job_id="job-001",
        kind="tensor.statistics",
        sha256="d" * 64,
        size_bytes=128,
        media_type="application/json",
    )
    completion = AnalysisCompletion(
        job_id="job-001",
        lease_id=lease.lease_id,
        lease_generation=lease.lease_generation,
        completion_key=lease.completion_key,
        outcome=CompletionOutcome.SUCCEEDED,
        artifacts=(artifact,),
    )

    completed = service.complete(completion)
    replay = service.complete(completion)

    assert running.state is ModelAnalysisJobState.RUNNING
    assert completed == replay
    assert completed.state is ModelAnalysisJobState.SUCCEEDED
    assert tasks.events[-1][0:3] == ("finish", "job-001", "succeeded")


def test_running_cancel_emits_a_fenced_signal() -> None:
    service = _service()
    queued = service.submit(_job(), idempotency_key="request-key-001")
    running, lease = service.claim(
        tenant_id="tenant-001",
        job_id="job-001",
        worker_id="worker-001",
        expected_version=queued.version,
        lease_seconds=30,
        max_memory_bytes=2048,
    )

    cancelled, signal = service.request_cancel(
        tenant_id="tenant-001",
        job_id="job-001",
        expected_version=running.version,
    )

    assert cancelled.state is ModelAnalysisJobState.CANCEL_REQUESTED
    assert signal is not None
    assert signal.lease_id == lease.lease_id
    assert signal.lease_generation == lease.lease_generation


def test_recovery_retries_submission_and_requeues_expired_lease() -> None:
    now = [1000]
    tasks = _Tasks(fail_submissions=1)
    service = _service(tasks, clock=lambda: now[0])
    with pytest.raises(ModelAnalysisJobServiceError):
        service.submit(_job(), idempotency_key="request-key-001")

    submission = service.recover()
    assert submission.recovered == 1
    queued = service.get(tenant_id="tenant-001", job_id="job-001")
    running, lease = service.claim(
        tenant_id="tenant-001",
        job_id="job-001",
        worker_id="worker-001",
        expected_version=queued.version,
        lease_seconds=1,
        max_memory_bytes=2048,
    )
    now[0] = lease.expires_epoch_ms

    recovery = service.recover()
    recovered = service.get(tenant_id="tenant-001", job_id="job-001")

    assert running.state is ModelAnalysisJobState.RUNNING
    assert recovery.requeued == 1
    assert recovered.state is ModelAnalysisJobState.QUEUED
    assert recovered.lease is None


def test_hub_task_port_disables_worker_orchestration() -> None:
    class _Queue:
        def __init__(self):
            self.values = None

        def ingest_task(self, **values):
            self.values = values

    queue = _Queue()
    reference = HubModelAnalysisTaskSubmissionPort(queue=queue).submit(_job())
    context = queue.values["extra_fields"]["worker_execution_context"][
        "model_intelligence"
    ]

    assert reference.parent_task_id == "task-001"
    assert context["persistence_owner"] == "hub"
    assert context["followup_task_creation_allowed"] is False
    assert context["peer_transfer_allowed"] is False
