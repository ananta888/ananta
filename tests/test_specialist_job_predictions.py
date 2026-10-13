"""Real SQL authorization and confined storage; all job/model evidence is synthetic."""

import hashlib
import os

import pytest

from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_training_artifact_binding import MlInternTrainingArtifactBinding
from agent.services.specialist_dataset_binding import BINDING_KEY, specialist_dataset_binding
from agent.services.specialist_job_predictions import SpecialistJobPredictionService
from agent.services.specialist_prediction_storage import ConfinedSpecialistPredictionStorage
from agent.services.unsloth_storage_governance_service import tenant_scope_digest
from tests.test_ml_intern_training_control_service import _create_dataset, _principal
from tests.test_specialist_prediction_artifact import encode, fixture_rows


@pytest.fixture
def admitted(app, tmp_path):
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    datasets = tmp_path / "datasets"
    artifacts = tmp_path / "artifacts"
    datasets.mkdir()
    artifacts.mkdir()
    dataset = _create_dataset(repository, principal, datasets / "train.jsonl")
    records, rows = fixture_rows()
    import json

    validation = datasets / "validation.jsonl"
    validation.write_bytes(b"".join((json.dumps(record) + "\n").encode() for record in records))
    dataset.validation_storage_ref = str(validation)
    dataset.validation_record_count = len(records)
    dataset.dataset_metadata = {
        "partition_hashes": {
            "train": dataset.content_sha256,
            "validation": hashlib.sha256(validation.read_bytes()).hexdigest(),
        }
    }
    dataset = repository.save_dataset(dataset, expected_version=dataset.version)
    body = encode(rows)
    job, _ = repository.create_job(
        MlInternTrainingJobDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id="synthetic-specialist-evaluation",
            dataset_id=dataset.id,
            job_type="evaluate_lora",
            status="completed",
            idempotency_key_digest="a" * 64,
            request_digest="b" * 64,
            request_spec={
                "task_family": "specialist_decision",
                "scorer_name": "specialist_decision",
                BINDING_KEY: specialist_dataset_binding(dataset),
            },
            result_summary={
                "artifacts": [
                    {
                        "name": "specialist_predictions.jsonl",
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "size_bytes": len(body),
                    }
                ]
            },
        )
    )
    attempt = repository.create_attempt(
        MlInternTrainingAttemptDB(
            job_id=job.id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            attempt_number=1,
            status="completed",
            worker_id="synthetic-worker",
            worker_url="http://synthetic-worker:8095",
            fencing_token_digest="c" * 64,
            lease_expires_at=1.0,
            deadline_at=2.0,
        )
    )
    job.active_attempt_id = attempt.id
    job = repository.save_job(job, expected_version=job.version)
    storage = MlInternTrainingArtifactBinding(tenant_scope_digest(principal), job.id, attempt.id)
    path = artifacts / storage.relative_directory("artifacts") / "specialist_predictions.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    service = SpecialistJobPredictionService(
        repository=repository,
        storage=ConfinedSpecialistPredictionStorage(artifact_root=artifacts, dataset_root=datasets),
    )
    return service, repository, principal, job, dataset, attempt, path, validation


def test_completed_scoped_job_returns_all_raw_outputs_even_after_lease_expiry(admitted):
    service, _repo, principal, job, *_ = admitted
    outputs = service.read(principal, job.id)
    assert len(outputs.adapter) == 25
    assert outputs.adapter == ('{"label":"allow"}',) * 25


def test_foreign_tenant_cannot_read_completed_job(admitted):
    service, _repo, _principal_value, job, *_ = admitted
    with pytest.raises(ValueError, match="job_unavailable"):
        service.read(_principal(), job.id)


@pytest.mark.parametrize(
    "mutation", ["job_status", "attempt_status", "dataset", "bytes", "artifact", "duplicate_metadata"]
)
def test_mutated_authority_or_bytes_are_rejected(admitted, mutation):
    service, repo, principal, job, dataset, attempt, path, validation = admitted
    if mutation == "job_status":
        job.status = "running"
        repo.save_job(job, expected_version=job.version)
    elif mutation == "attempt_status":
        attempt.status = "interrupted"
        repo.save_attempt(attempt, expected_version=attempt.version)
    elif mutation == "dataset":
        dataset.validation_record_count += 1
        repo.save_dataset(dataset, expected_version=dataset.version)
    elif mutation == "bytes":
        validation.write_bytes(validation.read_bytes().replace(b"case 0", b"case X", 1))
    elif mutation == "artifact":
        path.write_bytes(path.read_bytes().replace(b"allow", b"deny", 1))
    else:
        job.result_summary["artifacts"] *= 2
        repo.save_job(job, expected_version=job.version)
    with pytest.raises(ValueError):
        service.read(principal, job.id)


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo", "parent_symlink"])
def test_unsafe_artifact_storage_is_refused_without_blocking(admitted, unsafe, tmp_path):
    service, _repo, principal, job, _dataset, _attempt, path, _validation = admitted
    body = path.read_bytes()
    path.unlink()
    other = tmp_path / "other"
    other.write_bytes(body)
    if unsafe == "symlink":
        path.symlink_to(other)
    elif unsafe == "hardlink":
        os.link(other, path)
    elif unsafe == "fifo":
        os.mkfifo(path)
    else:
        parent = path.parent
        parent.rmdir()
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        (replacement / path.name).write_bytes(body)
        parent.symlink_to(replacement, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        service.read(principal, job.id)


def test_changed_job_during_byte_read_is_rejected_at_final_recheck(admitted, monkeypatch):
    service, repo, principal, job, *_ = admitted
    original = repo.get_job
    calls = 0

    def get_job(*args):
        nonlocal calls
        calls += 1
        current = original(*args)
        if calls == 2:
            current.status = "interrupted"
            current = repo.save_job(current, expected_version=current.version)
        return current

    monkeypatch.setattr(repo, "get_job", get_job)
    with pytest.raises(ValueError, match="job_changed"):
        service.read(principal, job.id)
