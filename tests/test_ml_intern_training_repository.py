from __future__ import annotations

import hashlib
import uuid

import pytest

from agent.db_models import MlInternDatasetDB, MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    MlInternTrainingRepositoryConflict,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


def _principal() -> MlInternTrainingPrincipal:
    suffix = uuid.uuid4().hex
    return MlInternTrainingPrincipal(f"tenant-{suffix}", f"admin-{suffix}")


def _dataset(principal: MlInternTrainingPrincipal, storage_ref: str) -> MlInternDatasetDB:
    digest = hashlib.sha256(storage_ref.encode()).hexdigest()
    return MlInternDatasetDB(
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        name="dataset.jsonl",
        content_sha256=digest,
        storage_ref=storage_ref,
        size_bytes=10,
        record_count=1,
    )


def test_dataset_is_idempotent_by_tenant_and_hash(app) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    created, replayed = repository.create_dataset(_dataset(principal, "/private/train.jsonl"))
    duplicate, duplicate_replayed = repository.create_dataset(_dataset(principal, "/private/train.jsonl"))
    assert replayed is False
    assert duplicate_replayed is True
    assert duplicate.id == created.id
    assert repository.get_dataset(principal, created.id) is not None
    assert repository.get_dataset(MlInternTrainingPrincipal("other", "other"), created.id) is None


def test_job_idempotency_conflict_and_event_cursor(app) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset, _ = repository.create_dataset(_dataset(principal, f"/{uuid.uuid4().hex}.jsonl"))
    base = dict(
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        task_id=f"task-{uuid.uuid4()}",
        dataset_id=dataset.id,
        idempotency_key_digest=hashlib.sha256(b"idempotency").hexdigest(),
        request_digest=hashlib.sha256(b"request-a").hexdigest(),
    )
    job, replayed = repository.create_job(MlInternTrainingJobDB(**base))
    same, same_replayed = repository.create_job(MlInternTrainingJobDB(**base))
    assert replayed is False and same_replayed is True and same.id == job.id

    with pytest.raises(MlInternTrainingRepositoryConflict, match="idempotency_payload_conflict"):
        repository.create_job(
            MlInternTrainingJobDB(**{**base, "request_digest": hashlib.sha256(b"request-b").hexdigest()})
        )

    event = repository.append_event(
        principal,
        job.id,
        event_type="queued",
        dedupe_key="queued-once",
        payload={"status": "queued"},
    )
    replay = repository.append_event(
        principal,
        job.id,
        event_type="queued",
        dedupe_key="queued-once",
        payload={"status": "queued"},
    )
    next_event = repository.append_event(
        principal,
        job.id,
        event_type="running",
        dedupe_key="running-once",
        payload={"status": "running"},
    )
    assert replay.id == event.id
    assert next_event.sequence == event.sequence + 1
    assert [item.id for item in repository.list_events(principal, job.id, after_sequence=event.sequence, limit=10)] == [
        next_event.id
    ]


def test_job_save_uses_optimistic_version(app) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset, _ = repository.create_dataset(_dataset(principal, f"/{uuid.uuid4().hex}.jsonl"))
    job, _ = repository.create_job(
        MlInternTrainingJobDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=f"task-{uuid.uuid4()}",
            dataset_id=dataset.id,
            idempotency_key_digest=uuid.uuid4().hex,
            request_digest=uuid.uuid4().hex,
        )
    )
    original_version = job.version
    job.phase = "preparing"
    saved = repository.save_job(job, expected_version=original_version)
    assert saved.version == original_version + 1
    with pytest.raises(MlInternTrainingRepositoryConflict, match="job_version_conflict"):
        repository.save_job(job, expected_version=original_version)


def test_attempt_history_is_ordered_and_allocates_next_attempt_number(app) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    job, _ = repository.create_job(
        MlInternTrainingJobDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=f"task-{uuid.uuid4()}",
            idempotency_key_digest=uuid.uuid4().hex,
            request_digest=uuid.uuid4().hex,
        )
    )
    attempts = [
        repository.create_attempt(
            MlInternTrainingAttemptDB(
                job_id=job.id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                attempt_number=number,
                status="interrupted" if number == 1 else "running",
                worker_id=f"worker-{number}",
                worker_url=f"internal://worker-{number}",
                fencing_token_digest=hashlib.sha256(f"token-{number}".encode()).hexdigest(),
                lease_expires_at=10_000 + number,
                deadline_at=20_000 + number,
            )
        )
        for number in (1, 2)
    ]

    assert repository.next_attempt_number(job.id) == 3
    assert [attempt.id for attempt in repository.list_attempts(job.id)] == [
        attempts[1].id,
        attempts[0].id,
    ]
    assert repository.get_attempt(attempts[0].id).attempt_number == 1
