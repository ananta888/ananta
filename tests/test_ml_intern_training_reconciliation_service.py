from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest
from sqlmodel import Session, select

from ananta_contracts.unsloth_capability import compose_worker_capability_probe
from agent.database import engine
from agent.db_models import (
    AuditLogDB,
    MlInternDatasetDB,
    MlInternTrainingAttemptDB,
    MlInternTrainingJobDB,
)
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.ml_intern_training_reconciliation_service import (
    MlInternTrainingReconciliationPolicy,
    MlInternTrainingReconciliationService,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


@pytest.fixture(autouse=True)
def _cleanup_created_active_jobs(app):
    """Keep Hub-global capacity state isolated from following test modules."""

    del app
    repository = MlInternTrainingRepository()
    existing_ids = {job.id for job in repository.list_active_jobs(limit=10_000)}
    yield
    for job in repository.list_active_jobs(limit=10_000):
        if job.id in existing_ids:
            continue
        expected = job.version
        job.status = "failed"
        job.phase = "test_cleanup"
        job.active_attempt_id = None
        job.worker_job_id = None
        job.retryable = False
        job.finished_at = time.time()
        repository.save_job(job, expected_version=expected)


class RecordingScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[MlInternTrainingPrincipal, str]] = []
        self.draining = False

    def schedule_reconciled_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> bool:
        if self.draining:
            return False
        self.calls.append((principal, job_id))
        return True

    def begin_shutdown(self) -> None:
        self.draining = True


class PrincipalScopedRepository:
    """Test adapter that isolates Hub-wide scans to one generated tenant."""

    def __init__(
        self,
        repository: MlInternTrainingRepository,
        principal: MlInternTrainingPrincipal,
    ) -> None:
        self._repository = repository
        self._principal = principal

    def list_active_jobs(self, *, limit: int = 1000) -> list[MlInternTrainingJobDB]:
        rows = self._repository.list_active_jobs(limit=10_000)
        scoped = [
            row
            for row in rows
            if row.tenant_id == self._principal.tenant_id and row.owner_subject == self._principal.subject
        ]
        return scoped[:limit]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)


class HoldingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, callback: Callable[..., Any], *args: Any) -> None:
        self.calls.append((callback, args))


class InlineExecutor:
    def submit(self, callback: Callable[..., Any], *args: Any) -> None:
        callback(*args)


class FakeTaskQueue:
    def ingest_task(self, **_values: Any) -> None:
        return None


class ReconcileDuringExecutionPort:
    worker_id = "worker-fencing-test"
    worker_ref = "internal://worker-fencing-test"

    def __init__(self) -> None:
        self.reconcile: Callable[[], None] | None = None

    def capability_probe(self) -> dict[str, Any]:
        return _nvidia_peft_capability_probe()

    def execute(
        self,
        *,
        job_id: str,
        spec: Mapping[str, Any],
        dataset_path: Path,
        validation_path: Path | None,
        attempt_id: str,
        fencing_token: int,
        on_event: Callable[[Mapping[str, Any]], None],
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]:
        del job_id, spec, dataset_path, validation_path, attempt_id, fencing_token, cancel_check
        assert self.reconcile is not None
        self.reconcile()
        on_event(
            {
                "type": "progress",
                "event_id": "late-event",
                "phase": "late-worker",
                "progress_percent": 99,
            }
        )
        return {
            "status": "completed",
            "result_ref": "training-result:must-not-win-after-fence",
        }


class CheckpointThenResumePort:
    worker_id = "worker-resume-test"
    worker_ref = "internal://worker-resume-test"

    def __init__(self) -> None:
        self.reconcile: Callable[[], None] | None = None
        self.specs: list[dict[str, Any]] = []
        self.fencing_tokens: list[int] = []
        self.checkpoint: dict[str, Any] | None = None

    def capability_probe(self) -> dict[str, Any]:
        return _nvidia_peft_capability_probe()

    def execute(
        self,
        *,
        job_id: str,
        spec: Mapping[str, Any],
        dataset_path: Path,
        validation_path: Path | None,
        attempt_id: str,
        fencing_token: int,
        on_event: Callable[[Mapping[str, Any]], None],
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]:
        del dataset_path, validation_path, cancel_check
        self.specs.append(copy.deepcopy(dict(spec)))
        self.fencing_tokens.append(fencing_token)
        if len(self.specs) == 1:
            self.checkpoint = {
                "relative_path": f"jobs/{job_id}/attempts/{attempt_id}/checkpoints/checkpoint-1.json",
                "binding": {
                    "job_id": job_id,
                    "source_attempt_id": attempt_id,
                    "base_model_hash": "a" * 64,
                    "dataset_hash": "b" * 64,
                    "configuration_hash": "c" * 64,
                    "checkpoint_sha256": "d" * 64,
                },
            }
            on_event(
                {
                    "type": "checkpoint",
                    "event_id": "checkpoint-1",
                    "phase": "checkpoint",
                    "resume_checkpoint": self.checkpoint,
                }
            )
            assert self.reconcile is not None
            self.reconcile()
            return {"status": "completed", "result_ref": "training-result:fenced"}
        assert self.checkpoint is not None
        assert spec.get("resume_checkpoint") == self.checkpoint
        return {
            "status": "completed",
            "result_ref": "training-result:resumed",
            "resume_checkpoint": self.checkpoint,
            "metrics": {"resumed": True},
            "artifacts": [],
        }


def _nvidia_peft_capability_probe() -> dict[str, Any]:
    return compose_worker_capability_probe(
        contract_version="lora-training.v1",
        resource_profile="nvidia",
        active_gpu_profile="rtx3080-safe",
        backend_availability={"peft_trl": (True, None)},
        package_versions={},
        hardware={
            "cuda_available": True,
            "total_vram_bytes": 10 * 1024**3,
        },
        runtime_ready=True,
    )


def _principal() -> MlInternTrainingPrincipal:
    suffix = uuid.uuid4().hex
    return MlInternTrainingPrincipal(f"tenant-{suffix}", f"admin-{suffix}")


def _job(
    repository: MlInternTrainingRepository,
    principal: MlInternTrainingPrincipal,
    *,
    status: str,
    updated_at: float,
    active_attempt_id: str | None = None,
    result_ref: str | None = None,
) -> MlInternTrainingJobDB:
    unique = uuid.uuid4().hex
    created, replayed = repository.create_job(
        MlInternTrainingJobDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=f"task-{unique}",
            dataset_id=None,
            status=status,
            phase=status,
            idempotency_key_digest=hashlib.sha256(f"idem-{unique}".encode()).hexdigest(),
            request_digest=hashlib.sha256(f"request-{unique}".encode()).hexdigest(),
            request_spec={
                "prompt": "TRAINING-CONTENT-MUST-NOT-ENTER-AUDIT",
                "api_key": "SECRET-MUST-NOT-ENTER-AUDIT",
            },
            active_attempt_id=active_attempt_id,
            worker_job_id=f"worker-job-{unique}" if active_attempt_id else None,
            result_ref=result_ref,
            updated_at=updated_at,
        )
    )
    assert replayed is False
    return created


def _attempt(
    repository: MlInternTrainingRepository,
    job: MlInternTrainingJobDB,
    *,
    attempt_id: str | None = None,
    number: int = 1,
    now: float,
    lease_expires_at: float,
    checkpoint_ref: str | None = None,
) -> MlInternTrainingAttemptDB:
    return repository.create_attempt(
        MlInternTrainingAttemptDB(
            id=attempt_id or f"lora-attempt-{uuid.uuid4()}",
            job_id=job.id,
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
            attempt_number=number,
            status="running",
            worker_id="worker-test",
            worker_url="internal://worker-test",
            fencing_token_digest=hashlib.sha256(b"raw-fencing-token").hexdigest(),
            lease_expires_at=lease_expires_at,
            deadline_at=now + 3_600,
            last_heartbeat_at=now - 100,
            checkpoint_ref=checkpoint_ref,
        )
    )


def _policy(*, max_attempts: int = 3) -> MlInternTrainingReconciliationPolicy:
    return MlInternTrainingReconciliationPolicy(
        heartbeat_timeout_seconds=10,
        queued_stale_seconds=10,
        cancel_grace_seconds=10,
        max_attempts=max_attempts,
        retry_stale_attempts=True,
        resume_from_checkpoint=True,
        batch_limit=20,
    )


def test_stale_attempt_is_fenced_retried_from_checkpoint_and_audited_without_content(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    attempt_id = f"lora-attempt-{uuid.uuid4()}"
    job = _job(
        repository,
        principal,
        status="running",
        updated_at=now - 100,
        active_attempt_id=attempt_id,
    )
    original = _attempt(
        repository,
        job,
        attempt_id=attempt_id,
        now=now,
        lease_expires_at=now - 1,
        checkpoint_ref="checkpoint-17",
    )
    scheduler = RecordingScheduler()
    audit_rows: list[tuple[str, dict[str, Any]]] = []
    service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        scheduler,
        policy=_policy(),
        audit=lambda action, details: audit_rows.append((action, details)),
        clock=lambda: now,
        is_hub=lambda: True,
    )

    summary = service.run_once()

    recovered = repository.get_job(principal, job.id)
    fenced = repository.get_attempt(original.id)
    assert summary["retried"] == 1
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.phase == "retry_queued_resume"
    assert recovered.active_attempt_id is None
    assert recovered.checkpoint_ref == "checkpoint-17"
    assert fenced is not None
    assert fenced.status == "interrupted"
    assert fenced.lease_expires_at == now
    assert fenced.fencing_token_digest != original.fencing_token_digest
    assert scheduler.calls == [(principal, job.id)]
    events = repository.list_events(principal, job.id, after_sequence=0, limit=20)
    assert [event.event_type for event in events] == ["interrupted", "retry_queued"]
    assert audit_rows[0][0] == "ml_intern_training_reconciled"
    details = audit_rows[0][1]
    assert details["actor"] == "hub:ml-intern-training-reconciler"
    assert details["reason_code"] == "attempt_lease_expired"
    assert details["task_id"] == job.task_id
    assert details["job_id"] == job.id
    assert details["attempt_id"] == original.id
    serialized_audit = json.dumps(audit_rows)
    assert "TRAINING-CONTENT" not in serialized_audit
    assert "SECRET-MUST" not in serialized_audit


def test_retry_budget_exhaustion_fails_job_without_redispatch(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    attempt_id = f"lora-attempt-{uuid.uuid4()}"
    job = _job(
        repository,
        principal,
        status="running",
        updated_at=now - 100,
        active_attempt_id=attempt_id,
    )
    _attempt(
        repository,
        job,
        attempt_id=attempt_id,
        number=3,
        now=now,
        lease_expires_at=now - 1,
    )
    scheduler = RecordingScheduler()
    service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        scheduler,
        policy=_policy(max_attempts=3),
        audit=lambda _action, _details: None,
        clock=lambda: now,
        is_hub=lambda: True,
    )

    summary = service.run_once()

    failed = repository.get_job(principal, job.id)
    assert summary["failed"] == 1
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "recovery_retry_budget_exhausted"
    assert failed.retryable is False
    assert scheduler.calls == []


def test_cancel_deadline_interrupts_even_with_fresh_worker_lease(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    attempt_id = f"lora-attempt-{uuid.uuid4()}"
    job = _job(
        repository,
        principal,
        status="cancel_requested",
        updated_at=now - 100,
        active_attempt_id=attempt_id,
    )
    job.cancel_requested = True
    repository.save_job(job, expected_version=job.version)
    attempt = _attempt(
        repository,
        job,
        attempt_id=attempt_id,
        now=now + 100,
        lease_expires_at=now + 100,
    )
    scheduler = RecordingScheduler()
    service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        scheduler,
        policy=_policy(),
        audit=lambda _action, _details: None,
        clock=lambda: now,
        is_hub=lambda: True,
    )

    summary = service.run_once()

    cancelled = repository.get_job(principal, job.id)
    interrupted = repository.get_attempt(attempt.id)
    assert summary["cancelled"] == 1
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.error_code == "cancel_deadline_exceeded"
    assert interrupted is not None and interrupted.status == "interrupted"
    assert scheduler.calls == []


def test_run_once_is_bounded_hub_only_and_shutdown_defers_new_claims(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    _job(repository, principal, status="queued", updated_at=now - 200)
    _job(repository, principal, status="queued", updated_at=now - 100)
    scheduler = RecordingScheduler()
    service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        scheduler,
        policy=_policy(),
        audit=lambda _action, _details: None,
        clock=lambda: now,
        is_hub=lambda: True,
    )

    summary = service.run_once(limit=1)

    assert summary["scanned"] == 1
    assert summary["redispatched"] == 1
    assert len(scheduler.calls) == 1
    service.begin_shutdown()
    draining = service.run_once(limit=20)
    assert draining["draining"] is True
    assert len(scheduler.calls) == 1

    worker_scheduler = RecordingScheduler()
    worker_service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        worker_scheduler,
        policy=_policy(),
        audit=lambda _action, _details: None,
        clock=lambda: now,
        is_hub=lambda: False,
    )
    assert worker_service.run_once()["hub_only"] is False
    assert worker_scheduler.calls == []


def test_default_audit_sink_persists_content_free_recovery_history(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    job = _job(repository, principal, status="queued", updated_at=now - 100)
    service = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        RecordingScheduler(),
        policy=_policy(),
        clock=lambda: now,
        is_hub=lambda: True,
    )

    assert service.run_once()["redispatched"] == 1

    with Session(engine) as session:
        row = session.exec(
            select(AuditLogDB)
            .where(
                AuditLogDB.action == "ml_intern_training_reconciled",
                AuditLogDB.task_id == job.task_id,
            )
            .order_by(AuditLogDB.id.desc())
        ).first()
    assert row is not None
    assert row.details["actor"] == "hub:ml-intern-training-reconciler"
    assert row.details["reason_code"] == "stale_queued_job"
    assert row.details["task_id"] == job.task_id
    assert row.details["job_id"] == job.id
    serialized = json.dumps(row.details)
    assert "TRAINING-CONTENT" not in serialized
    assert "SECRET-MUST" not in serialized


def test_restart_preserves_terminal_result_job_list_and_event_cursor(app) -> None:
    del app
    repository_before_restart = MlInternTrainingRepository()
    principal = _principal()
    job = _job(
        repository_before_restart,
        principal,
        status="completed",
        updated_at=time.time(),
        result_ref="training-result:durable-reference",
    )
    first = repository_before_restart.append_event(
        principal,
        job.id,
        event_type="running",
        dedupe_key="restart-running",
        payload={"status": "running"},
    )
    terminal = repository_before_restart.append_event(
        principal,
        job.id,
        event_type="completed",
        dedupe_key="restart-completed",
        payload={"status": "completed"},
    )

    repository_after_restart = MlInternTrainingRepository()
    restarted_scheduler = RecordingScheduler()
    restarted = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository_after_restart, principal),
        restarted_scheduler,
        policy=_policy(),
        audit=lambda _action, _details: None,
        is_hub=lambda: True,
    )
    assert restarted.run_once()["scanned"] == 0
    jobs = repository_after_restart.list_jobs(principal, limit=20, offset=0)
    events = repository_after_restart.list_events(
        principal,
        job.id,
        after_sequence=first.sequence,
        limit=20,
    )
    assert jobs[0].id == job.id
    assert jobs[0].result_ref == "training-result:durable-reference"
    assert [event.id for event in events] == [terminal.id]
    assert terminal.sequence == first.sequence + 1


def test_restart_resumes_job_left_in_interrupted_handoff_state(app) -> None:
    del app
    now = time.time() + 1_000
    repository = MlInternTrainingRepository()
    principal = _principal()
    job = _job(
        repository,
        principal,
        status="interrupted",
        updated_at=now - 100,
    )
    job.error_code = "attempt_heartbeat_stale"
    job.retryable = True
    repository.save_job(job, expected_version=job.version)
    attempt = _attempt(
        repository,
        job,
        number=1,
        now=now,
        lease_expires_at=now - 1,
        checkpoint_ref="checkpoint-before-hub-restart",
    )
    attempt.status = "interrupted"
    attempt.finished_at = now - 1
    repository.save_attempt(attempt, expected_version=attempt.version)

    scheduler = RecordingScheduler()
    restarted = MlInternTrainingReconciliationService(
        PrincipalScopedRepository(repository, principal),
        scheduler,
        policy=_policy(),
        audit=lambda _action, _details: None,
        clock=lambda: now,
        is_hub=lambda: True,
    )

    summary = restarted.run_once()

    recovered = repository.get_job(principal, job.id)
    assert summary["retried"] == 1
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.phase == "retry_queued_resume"
    assert recovered.checkpoint_ref == "checkpoint-before-hub-restart"
    assert scheduler.calls == [(principal, job.id)]


def test_control_shutdown_blocks_queued_claim_and_late_fenced_result_is_ignored(
    app,
    tmp_path,
    monkeypatch,
) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    queued = _job(repository, principal, status="queued", updated_at=time.time())
    holding = HoldingExecutor()
    draining_control = MlInternTrainingControlService(
        {"enabled": True},
        repository=repository,
        executor=holding,
    )
    assert draining_control.schedule_reconciled_job(principal, queued.id) is True
    draining_control.begin_shutdown()
    callback, args = holding.calls[0]
    callback(*args)
    assert repository.list_attempts(queued.id) == []
    assert draining_control.schedule_reconciled_job(principal, queued.id) is False

    training_path = tmp_path / "train.jsonl"
    validation_path = tmp_path / "validation.jsonl"
    training_path.write_text('{"instruction":"hello","output":"world"}\n', encoding="utf-8")
    validation_path.write_text('{"instruction":"hello","output":"world"}\n', encoding="utf-8")
    dataset, _ = repository.create_dataset(
        MlInternDatasetDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            name="fencing.jsonl",
            status="ready",
            content_sha256=hashlib.sha256(training_path.read_bytes()).hexdigest(),
            size_bytes=training_path.stat().st_size,
            record_count=2,
            train_record_count=1,
            validation_record_count=1,
            storage_ref=str(training_path),
            train_storage_ref=str(training_path),
            validation_storage_ref=str(validation_path),
            validation_report={"ok": True},
        )
    )
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    clock_value = [1_000.0]
    port = ReconcileDuringExecutionPort()
    live_control = MlInternTrainingControlService(
        {
            "enabled": True,
            "mode": "live",
            "timeout_seconds": 60,
            "max_concurrent_jobs": 16,
            "base_models": ["local-test-model"],
        },
        repository=repository,
        execution_port=port,
        executor=InlineExecutor(),
        clock=lambda: clock_value[0],
    )
    recovery_scheduler = RecordingScheduler()

    def reconcile_live_attempt() -> None:
        clock_value[0] = 2_000.0
        result = MlInternTrainingReconciliationService(
            PrincipalScopedRepository(repository, principal),
            recovery_scheduler,
            policy=_policy(),
            audit=lambda _action, _details: None,
            clock=lambda: clock_value[0],
            is_hub=lambda: True,
        ).run_once()
        assert result["retried"] == 1

    port.reconcile = reconcile_live_attempt
    accepted, _ = live_control.create_job(
        principal,
        {
            "dataset_id": dataset.id,
            "job_type": "train_lora",
            "mode": "live",
            "backend": "peft_trl",
            "base_model": "local-test-model",
            "live_confirmed": True,
            "risk_reason": "exercise stale attempt fencing",
            "hyperparameters": {"max_steps": 2},
        },
        idempotency_key=f"fencing-{uuid.uuid4().hex}",
    )

    fenced_job = repository.get_job(principal, accepted["id"])
    attempts = repository.list_attempts(accepted["id"])
    assert fenced_job is not None
    assert fenced_job.status == "queued"
    assert fenced_job.phase == "retry_queued"
    assert fenced_job.result_ref is None
    assert fenced_job.progress_percent == 0.0
    assert attempts[0].status == "interrupted"
    assert recovery_scheduler.calls == [(principal, accepted["id"])]
    events = repository.list_events(principal, accepted["id"], after_sequence=0, limit=50)
    assert "late-worker" not in {str(event.payload.get("phase")) for event in events}
    stale = [event for event in events if event.event_type == "stale_attempt_signal_ignored"]
    assert {event.payload["signal_type"] for event in stale} == {"event", "result"}
    assert all("late-worker" not in str(event.payload) for event in stale)


def test_checkpoint_resume_roundtrip_preserves_immutable_request_digest(
    app,
    tmp_path,
    monkeypatch,
) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    training_path = tmp_path / "resume-train.jsonl"
    validation_path = tmp_path / "resume-validation.jsonl"
    training_path.write_text('{"instruction":"hello","output":"world"}\n', encoding="utf-8")
    validation_path.write_text('{"instruction":"validate","output":"world"}\n', encoding="utf-8")
    dataset, _ = repository.create_dataset(
        MlInternDatasetDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            name="resume.jsonl",
            status="ready",
            content_sha256=hashlib.sha256(training_path.read_bytes()).hexdigest(),
            size_bytes=training_path.stat().st_size,
            record_count=2,
            train_record_count=1,
            validation_record_count=1,
            storage_ref=str(training_path),
            train_storage_ref=str(training_path),
            validation_storage_ref=str(validation_path),
            validation_report={"ok": True},
        )
    )
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    clock_value = [1_000.0]
    port = CheckpointThenResumePort()
    control = MlInternTrainingControlService(
        {
            "enabled": True,
            "mode": "live",
            "timeout_seconds": 60,
            "max_concurrent_jobs": 16,
            "base_models": ["local-test-model"],
        },
        repository=repository,
        execution_port=port,
        executor=InlineExecutor(),
        clock=lambda: clock_value[0],
    )
    recovery_scheduler = RecordingScheduler()

    def reconcile_checkpointed_attempt() -> None:
        clock_value[0] = 2_000.0
        result = MlInternTrainingReconciliationService(
            PrincipalScopedRepository(repository, principal),
            recovery_scheduler,
            policy=_policy(),
            audit=lambda _action, _details: None,
            clock=lambda: clock_value[0],
            is_hub=lambda: True,
        ).run_once()
        assert result["retried"] == 1

    port.reconcile = reconcile_checkpointed_attempt
    accepted, _ = control.create_job(
        principal,
        {
            "dataset_id": dataset.id,
            "job_type": "train_lora",
            "mode": "live",
            "backend": "peft_trl",
            "base_model": "local-test-model",
            "live_confirmed": True,
            "risk_reason": "exercise checkpoint resume fencing",
            "hyperparameters": {"max_steps": 2},
        },
        idempotency_key=f"resume-{uuid.uuid4().hex}",
    )
    after_fence = repository.get_job(principal, accepted["id"])
    assert after_fence is not None
    original_digest = after_fence.request_digest
    original_spec = copy.deepcopy(after_fence.request_spec)
    assert after_fence.status == "queued"
    assert after_fence.phase == "retry_queued_resume"
    assert after_fence.checkpoint_ref is not None
    assert after_fence.checkpoint_ref.startswith("lora-checkpoint-v1:")
    assert "resume_checkpoint" not in after_fence.request_spec

    assert control.schedule_reconciled_job(principal, accepted["id"]) is True

    completed = repository.get_job(principal, accepted["id"])
    attempts = repository.list_attempts(accepted["id"])
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_ref == "training-result:resumed"
    assert completed.request_digest == original_digest
    assert completed.request_spec == original_spec
    assert "resume_checkpoint" not in completed.request_spec
    assert port.checkpoint is not None
    assert port.specs[0].get("resume_checkpoint") is None
    assert port.specs[1]["resume_checkpoint"] == port.checkpoint
    assert completed.result_summary["resume_checkpoint"] == port.checkpoint
    assert len(port.fencing_tokens) == 2
    assert port.fencing_tokens[0] >> 128 == 1
    assert port.fencing_tokens[1] >> 128 == 2
    assert port.fencing_tokens[1] > port.fencing_tokens[0]
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (2, "completed"),
        (1, "interrupted"),
    ]
