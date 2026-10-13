"""Admission-to-execution dataset fencing with real Hub SQL metadata."""

import hashlib
from types import SimpleNamespace

import pytest

from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_training_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_dataset_binding import (
    BINDING_KEY,
    require_specialist_dataset_binding,
    require_specialist_partition,
    specialist_dataset_binding,
)
from tests.test_ml_intern_training_control_service import (
    FakeTaskQueue,
    HoldingExecutor,
    _create_dataset,
    _payload,
    _principal,
)


def spec(dataset):
    return {
        "dataset_id": dataset.id,
        "task_family": "specialist_decision",
        BINDING_KEY: specialist_dataset_binding(dataset),
    }


def dataset_projection():
    return SimpleNamespace(
        id="dataset-one",
        version=1,
        content_sha256="a" * 64,
        train_record_count=2,
        validation_record_count=1,
        dataset_metadata={"partition_hashes": {"train": "a" * 64, "validation": "b" * 64}},
    )


@pytest.mark.parametrize("field", ["version", "content_sha256", "train_record_count", "validation_record_count"])
def test_catalog_changes_are_rejected(field):
    dataset = dataset_projection()
    admitted = spec(dataset)
    setattr(dataset, field, "c" * 64 if field == "content_sha256" else 3)
    with pytest.raises(MlInternTrainingContractError, match="changed after job admission"):
        require_specialist_dataset_binding(admitted, dataset)


def test_coercive_pinned_version_is_not_equal_to_authoritative_integer():
    dataset = dataset_projection()
    admitted = spec(dataset)
    admitted[BINDING_KEY]["version"] = True
    with pytest.raises(MlInternTrainingContractError):
        require_specialist_dataset_binding(admitted, dataset)


@pytest.mark.parametrize("partition", ["train", "validation"])
def test_actual_transfer_partition_must_match_original_hash_and_count(partition):
    admitted = spec(dataset_projection())
    pinned = admitted[BINDING_KEY]
    manifest = {"sha256": pinned[f"{partition}_sha256"], "record_count": pinned[f"{partition}_records"]}
    require_specialist_partition(admitted, manifest, partition)
    manifest["sha256"] = "c" * 64
    with pytest.raises(MlInternTrainingContractError):
        require_specialist_partition(admitted, manifest, partition)


def test_real_hub_admission_pins_dataset_and_refuses_changed_catalog_before_execution(app, tmp_path, monkeypatch):
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "train.jsonl")
    validation = tmp_path / "validation.jsonl"
    validation.write_text('{"instruction":"case","output":"deny"}\n')
    dataset.validation_storage_ref = str(validation)
    dataset.validation_record_count = 1
    dataset.dataset_metadata = {
        "partition_hashes": {
            "train": dataset.content_sha256,
            "validation": hashlib.sha256(validation.read_bytes()).hexdigest(),
        }
    }
    dataset = repository.save_dataset(dataset, expected_version=dataset.version)
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: FakeTaskQueue()
    )
    executor = HoldingExecutor()
    service = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "artifacts")}, repository=repository, executor=executor
    )
    payload = {
        **_payload(dataset.id),
        "task_family": "specialist_decision",
        "task_kinds": ["specialist_decision"],
        "output_schema_digest": "a" * 64,
        "serializer_digest": "b" * 64,
        "specialist": {
            "specialist_id": "code-risk-gate",
            "contract_version": "v1",
            "contract_digest": "a" * 64,
            "dataset_digest": "b" * 64,
            "holdout_digest": "c" * 64,
            "benchmark_version": "bench-v1",
        },
    }
    accepted, _replayed = service.create_job(principal, payload, idempotency_key="specialist-bound-dataset")
    job = repository.get_job(principal, accepted["id"])
    assert job.request_spec[BINDING_KEY] == specialist_dataset_binding(dataset)
    original_binding = dict(job.request_spec[BINDING_KEY])
    dataset.dataset_metadata["partition_hashes"]["validation"] = "d" * 64
    repository.save_dataset(dataset, expected_version=dataset.version)
    executions = []
    monkeypatch.setattr(service, "_execute_local_bounded", lambda *args: executions.append(args))
    callback, arguments = executor.calls[0]
    callback(*arguments)
    failed = repository.get_job(principal, accepted["id"])
    assert failed.status == "failed"
    assert failed.error_code == "specialist_dataset_binding_changed"
    assert failed.request_spec[BINDING_KEY] == original_binding
    assert executions == []
