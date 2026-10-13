"""Independent, replay-stable evaluation catalog partitions and Hub training refusal."""

import io
import json
from pathlib import Path

import pytest

from agent.services.ml_intern_training_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_dataset_admission import encode_specialist_partition
from agent.services.specialist_dataset_split import split_examples
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_ml_intern_training_control_service import FakeTaskQueue, HoldingExecutor, _payload
from tests.test_specialist_dataset_admission import setup_admission
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import gate_examples


def _prepared(tmp_path):
    admission, repo, principal = setup_admission(tmp_path)
    manifest, partitions = split_examples(gate_examples(45), seed=11)
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    return admission, repo, principal, manifest, {
        name: strategy.training_records(rows) for name, rows in partitions.items()
    }


def test_training_validation_and_holdout_have_distinct_stable_dataset_versions(app, tmp_path):
    del app
    admission, repo, principal, manifest, records = _prepared(tmp_path)
    training = admission.admit(manifest, records["train"], records["validation"])
    ids = {training.dataset_id}
    for role in ("validation", "holdout"):
        admitted = admission.admit_evaluation(manifest, records["train"], records[role], partition=role)
        assert admitted.dataset_id not in ids
        ids.add(admitted.dataset_id)
        dataset = repo.get_dataset(principal, admitted.dataset_id)
        assert dataset.split_manifest["execution_purpose"] == "specialist_evaluation"
        assert dataset.split_manifest["evaluation_partition"] == role
        actual = [json.loads(line) for line in Path(dataset.validation_storage_ref).read_text().splitlines()]
        assert actual == records[role]
        assert admission.admit_evaluation(manifest, records["train"], records[role], partition=role) == admitted
    assert repo.get_dataset(principal, training.dataset_id).version == training.version
    original = repo.get_dataset(principal, training.dataset_id)
    assert not set(manifest.partitions["holdout"]) & {
        json.loads(line)["record_digest"]
        for path in (original.train_storage_ref, original.validation_storage_ref)
        for line in Path(path).read_text().splitlines()
    }


@pytest.mark.parametrize("role", ["validation", "holdout"])
def test_real_hub_rejects_training_on_evaluation_only_dataset(app, tmp_path, monkeypatch, role):
    del app
    admission, repo, principal, manifest, records = _prepared(tmp_path)
    admitted = admission.admit_evaluation(manifest, records["train"], records[role], partition=role)
    queue = FakeTaskQueue()
    monkeypatch.setattr("agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: queue)
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "artifacts")},
        repository=repo, executor=HoldingExecutor(),
    )
    with pytest.raises(MlInternTrainingContractError) as error:
        control.create_job(principal, _payload(admitted.dataset_id), idempotency_key="forbidden-training")
    assert error.value.reason_code == "specialist_evaluation_dataset_training_forbidden"
    assert queue.tasks == []


def test_substituted_partition_is_rejected_before_catalog_admission(app, tmp_path):
    del app
    admission, repo, principal, manifest, records = _prepared(tmp_path)
    with pytest.raises(ValueError, match="partition_manifest_mismatch"):
        admission.admit_evaluation(manifest, records["train"], records["validation"], partition="holdout")
    assert repo.list_datasets(principal, limit=10, offset=0) == []


def test_catalog_resplit_cannot_remove_evaluation_purpose(app, tmp_path):
    del app
    admission, repo, principal, manifest, records = _prepared(tmp_path)
    admitted = admission.admit_evaluation(manifest, records["train"], records["holdout"], partition="holdout")
    dataset = repo.get_dataset(principal, admitted.dataset_id)
    summary = admission._catalog.commit_split(
        tenant_id=principal.tenant_id, principal_id=principal.subject,
        dataset_id=dataset.dataset_metadata["catalog_dataset_id"],
        train_stream=io.BytesIO(encode_specialist_partition(records["train"])),
        validation_stream=io.BytesIO(encode_specialist_partition(records["holdout"])),
        manifest={"algorithm_version": "resplit-test", "train_record_count": len(records["train"]),
                  "validation_record_count": len(records["holdout"])},
    )
    assert summary["split"]["execution_purpose"] == "specialist_evaluation"
    assert summary["split"]["evaluation_partition"] == "holdout"


def test_execution_rechecks_evaluation_purpose_after_job_admission(app, tmp_path, monkeypatch):
    del app
    admission, repo, principal, manifest, records = _prepared(tmp_path)
    admitted = admission.admit(manifest, records["train"], records["validation"])
    executor = HoldingExecutor()
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: FakeTaskQueue()
    )
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "artifacts")}, repository=repo, executor=executor
    )
    accepted, _ = control.create_job(principal, _payload(admitted.dataset_id), idempotency_key="purpose-recheck")
    dataset = repo.get_dataset(principal, admitted.dataset_id)
    dataset.split_manifest = {**dataset.split_manifest, "execution_purpose": "specialist_evaluation"}
    repo.save_dataset(dataset, expected_version=dataset.version)
    executions = []
    monkeypatch.setattr(control, "_execute_local_bounded", lambda *args: executions.append(args))
    callback, arguments = executor.calls[0]
    callback(*arguments)
    job = repo.get_job(principal, accepted["id"])
    assert job.status == "failed"
    assert job.error_code == "specialist_evaluation_dataset_training_forbidden"
    assert executions == []
