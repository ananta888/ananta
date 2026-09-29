from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from agent.repositories.model_analysis_job_repository import (
    SQLiteModelAnalysisJobRepository,
)
from agent.services.model_analysis_job_service import (
    ModelAnalysisJobService,
    ModelAnalysisJobServiceError,
    ModelAnalysisJobState,
)
from agent.services.model_analysis_task_port import ModelAnalysisTaskReference
from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CompletionOutcome,
)

MODEL_ID = f"model_{'a' * 64}"


def _job(number: int = 1) -> AnalysisJob:
    return AnalysisJob(
        job_id=f"job-{number:03d}",
        hub_task_id=f"task-{number:03d}",
        tenant_id="tenant-001",
        model_id=MODEL_ID,
        analysis_kind="static.tensor-statistics",
        profile_id="profile.static-safe.v1",
        request_sha256=f"{number:064x}",
        requested_artifact_kinds=("tensor.statistics",),
        max_runtime_seconds=60,
        max_output_bytes=4096,
    )


class _Tasks:
    def __init__(self) -> None:
        self.events = []

    def submit(self, job):
        self.events.append(("submit", job.job_id))
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


def _service(path, tasks, clock):
    return ModelAnalysisJobService(
        repository=SQLiteModelAnalysisJobRepository(path),
        tasks=tasks,
        epoch_ms=clock,
    )


def test_idempotency_and_terminal_completion_survive_restart(tmp_path) -> None:
    path = tmp_path / "model-analysis.sqlite3"
    tasks = _Tasks()
    service = _service(path, tasks, lambda: 1000)
    queued = service.submit(_job(), idempotency_key="request-key-001")
    running, lease = service.claim(
        tenant_id="tenant-001",
        job_id="job-001",
        worker_id="worker-001",
        expected_version=queued.version,
        lease_seconds=30,
        max_memory_bytes=2048,
    )
    completion = AnalysisCompletion(
        job_id="job-001",
        lease_id=lease.lease_id,
        lease_generation=lease.lease_generation,
        completion_key=lease.completion_key,
        outcome=CompletionOutcome.SUCCEEDED,
        artifacts=(
            ArtifactRef(
                artifact_id="artifact-001",
                job_id="job-001",
                kind="tensor.statistics",
                sha256="d" * 64,
                size_bytes=128,
                media_type="application/json",
            ),
        ),
    )
    completed = service.complete(completion)

    restarted = _service(path, _Tasks(), lambda: 1000)
    replay = restarted.submit(
        _job(),
        idempotency_key="request-key-001",
    )
    loaded = restarted.get(
        tenant_id="tenant-001",
        job_id="job-001",
    )

    assert running.state is ModelAnalysisJobState.RUNNING
    assert completed.state is ModelAnalysisJobState.SUCCEEDED
    assert replay == loaded == completed


def test_expired_lease_is_recovered_after_restart(tmp_path) -> None:
    path = tmp_path / "model-analysis.sqlite3"
    now = [1000]
    first = _service(path, _Tasks(), lambda: now[0])
    queued = first.submit(_job(), idempotency_key="request-key-001")
    _running, lease = first.claim(
        tenant_id="tenant-001",
        job_id="job-001",
        worker_id="worker-001",
        expected_version=queued.version,
        lease_seconds=1,
        max_memory_bytes=2048,
    )
    now[0] = lease.expires_epoch_ms

    restarted = _service(path, _Tasks(), lambda: now[0])
    summary = restarted.recover()
    recovered = restarted.get(
        tenant_id="tenant-001",
        job_id="job-001",
    )

    assert summary.requeued == 1
    assert recovered.state is ModelAnalysisJobState.QUEUED
    assert recovered.lease is None


def test_compare_and_set_rejects_stale_writer_from_other_connection(
    tmp_path,
) -> None:
    path = tmp_path / "model-analysis.sqlite3"
    service = _service(path, _Tasks(), lambda: 1000)
    queued = service.submit(_job(), idempotency_key="request-key-001")
    first = SQLiteModelAnalysisJobRepository(path)
    second = SQLiteModelAnalysisJobRepository(path)
    first_view = first.get("job-001")
    second_view = second.get("job-001")
    assert first_view == second_view == queued

    first.compare_and_set(
        replace(
            first_view,
            state=ModelAnalysisJobState.FAILED,
            version=first_view.version + 1,
            reason_code="fixture_failed",
            updated_epoch_ms=2000,
        ),
        expected_version=first_view.version,
    )
    with pytest.raises(ModelAnalysisJobServiceError) as raised:
        second.compare_and_set(
            replace(
                second_view,
                state=ModelAnalysisJobState.CANCELLED,
                version=second_view.version + 1,
                reason_code="fixture_cancelled",
                updated_epoch_ms=2000,
            ),
            expected_version=second_view.version,
        )
    assert raised.value.reason_code == "model_analysis_version_conflict"


def test_repository_has_tenant_queue_and_recovery_indices(tmp_path) -> None:
    path = tmp_path / "model-analysis.sqlite3"
    SQLiteModelAnalysisJobRepository(path)

    with sqlite3.connect(path) as connection:
        index_names = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA index_list('model_analysis_jobs')"
            ).fetchall()
        }

    assert "ix_model_analysis_jobs_tenant_state" in index_names
    assert "ix_model_analysis_jobs_queue" in index_names
    assert "ix_model_analysis_jobs_recovery" in index_names
