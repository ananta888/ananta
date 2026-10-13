"""Real catalog/SQL partitions in a synthetic Factory model cycle."""

import json

import pytest

from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_dataset_repository_bridge_service import MlInternDatasetRepositoryBridgeService
from agent.services.specialist_dataset_admission import HubSpecialistDatasetAdmission
from agent.services.specialist_dataset_split import split_examples
from agent.services.specialist_model_factory import SpecialistModelFactory
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_ml_intern_dataset_catalog_service import _catalog
from tests.test_ml_intern_training_control_service import _principal
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import MID, POLICY, TINY, LookupTrainer, gate_examples, plan_for


def setup_admission(tmp_path):
    principal = _principal()
    catalog = _catalog(tmp_path)
    repository = MlInternTrainingRepository()
    bridge = MlInternDatasetRepositoryBridgeService(
        execution_root=tmp_path / "execution", catalog=catalog, repository=repository, max_dataset_bytes=1024 * 1024
    )
    return HubSpecialistDatasetAdmission(catalog=catalog, bridge=bridge, principal=principal), repository, principal


def test_factory_uses_real_hub_dataset_without_holdout_in_training(app, tmp_path):
    del app
    admission, repository, principal = setup_admission(tmp_path)
    examples = gate_examples(45)
    manifest, partitions = split_examples(examples, seed=11)
    trainer = LookupTrainer({TINY: {}, MID: {}})
    factory = SpecialistModelFactory(
        GATE,
        trainer=trainer,
        predictor=trainer,
        registry=MlInternAdapterRegistryService(tmp_path / "registry.json"),
        policy=POLICY,
        benchmark_version="code-risk-bench-v1",
        dataset_admission=admission,
    )
    cycle = factory.run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 11), seed=11)
    assert cycle.winner is not None
    ids = {job.dataset_id for job in trainer.jobs}
    assert len(ids) == 1
    dataset_id = ids.pop()
    dataset = repository.get_dataset(principal, dataset_id)
    assert dataset is not None and dataset.validation_report["ok"]
    from pathlib import Path

    train = [json.loads(line) for line in Path(dataset.train_storage_ref).read_text().splitlines()]
    validation = [json.loads(line) for line in Path(dataset.validation_storage_ref).read_text().splitlines()]
    assert tuple(row["record_digest"] for row in train) == manifest.partitions["train"]
    assert tuple(row["record_digest"] for row in validation) == manifest.partitions["validation"]
    assert not set(manifest.partitions["holdout"]) & {row["record_digest"] for row in train + validation}
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    again = admission.admit(
        manifest, strategy.training_records(partitions["train"]), strategy.training_records(partitions["validation"])
    )
    assert again.dataset_id == dataset_id and again.version == dataset.version
    assert repository.get_dataset(principal, again.dataset_id).version == dataset.version
    assert all(job.request_spec["expected_dataset_version"] == dataset.version for job in trainer.jobs)


def test_admission_rejects_holdout_substitution_before_catalog_writes(app, tmp_path):
    del app
    admission, repository, principal = setup_admission(tmp_path)
    manifest, partitions = split_examples(gate_examples(45), seed=11)
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    with pytest.raises(ValueError, match="partition_manifest_mismatch"):
        admission.admit(
            manifest,
            strategy.training_records(partitions["holdout"]),
            strategy.training_records(partitions["validation"]),
        )
    assert repository.list_datasets(principal, limit=10, offset=0) == []


def test_partition_byte_quota_fails_before_catalog_or_sql_admission(app, tmp_path, monkeypatch):
    del app
    admission, repository, principal = setup_admission(tmp_path)
    manifest, partitions = split_examples(gate_examples(45), seed=11)
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    monkeypatch.setattr("agent.services.specialist_dataset_admission.MAX_PARTITION_BYTES", 1)
    with pytest.raises(ValueError, match="partition_quota_exceeded"):
        admission.admit(
            manifest,
            strategy.training_records(partitions["train"]),
            strategy.training_records(partitions["validation"]),
        )
    assert repository.list_datasets(principal, limit=10, offset=0) == []
