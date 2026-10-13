"""Optimistic dataset version condition before Hub job/task admission."""

import pytest

from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand, MlInternTrainingContractError
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from tests.test_ml_intern_training_control_service import (
    FakeTaskQueue,
    HoldingExecutor,
    _create_dataset,
    _payload,
    _principal,
)


@pytest.mark.parametrize("version", [True, False, None, 0, -1, 1.0, "1", 2**63])
def test_expected_version_is_a_strict_positive_json_integer(version):
    with pytest.raises(MlInternTrainingContractError) as error:
        CreateTrainingJobCommand.from_mapping({**_payload("dataset-one"), "expected_dataset_version": version})
    assert error.value.reason_code == "dataset_version_invalid"


@pytest.mark.parametrize("changed", [False, True])
def test_actual_sql_admission_honors_caller_dataset_version(app, tmp_path, monkeypatch, changed):
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "train.jsonl")
    expected_version = dataset.version
    if changed:
        dataset.name = "changed after factory admission"
        dataset = repository.save_dataset(dataset, expected_version=dataset.version)
    queue = FakeTaskQueue()
    monkeypatch.setattr("agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: queue)
    service = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "artifacts")},
        repository=repository,
        executor=HoldingExecutor(),
    )
    payload = {**_payload(dataset.id), "expected_dataset_version": expected_version}
    if changed:
        with pytest.raises(MlInternTrainingContractError) as error:
            service.admit_job(principal, payload, idempotency_key="synthetic-expected-version")
        assert error.value.reason_code == "dataset_version_conflict"
        assert queue.tasks == []
        assert repository.list_jobs(principal, limit=10, offset=0) == []
    else:
        accepted, replayed = service.admit_job(principal, payload, idempotency_key="synthetic-expected-version")
        assert not replayed and len(queue.tasks) == 1
        job = repository.get_job(principal, accepted["id"])
        assert job.request_spec["expected_dataset_version"] == expected_version
