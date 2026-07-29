import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.services.ml_intern_training_artifact_binding import (
    MlInternTrainingArtifactBinding,
)
from agent.services.ml_intern_training_result_publisher import (
    RegistryTrainingResultPublisher,
)


def test_result_publisher_propagates_only_hub_bound_provenance(tmp_path) -> None:
    publisher = RegistryTrainingResultPublisher(
        artifact_root=tmp_path,
        registry_path=tmp_path / "registry.json",
    )
    publisher._security = MagicMock()  # noqa: SLF001
    publisher._security.resolve_relative.return_value = tmp_path / "adapter"  # noqa: SLF001
    publisher._security.validate_adapter_tree.return_value = {  # noqa: SLF001
        "tree_sha256": "b" * 64,
        "total_bytes": 17,
    }
    publisher._registry = MagicMock()  # noqa: SLF001
    job = SimpleNamespace(
        id="job-1",
        request_spec={
            "method": "qlora",
            "dataset_hash": "c" * 64,
            "source_ids": ["SRC_training-corpus"],
            "run_ids": ["RUN_materialization-1"],
            "provenance_status": "verified",
        },
        base_model="local-model",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
        owner_subject="admin-1",
    )

    publisher.publish(job, {"adapter_id": "adapter-1"})

    values = publisher._registry.register_trained.call_args.kwargs  # noqa: SLF001
    assert values["dataset_hash"] == "c" * 64
    assert values["source_ids"] == ["SRC_training-corpus"]
    assert values["run_ids"] == ["RUN_materialization-1"]
    assert values["provenance_verified"] is True


def test_worker_binding_cannot_supply_missing_hub_attempt(tmp_path) -> None:
    publisher = RegistryTrainingResultPublisher(
        artifact_root=tmp_path,
        registry_path=tmp_path / "registry.json",
    )
    publisher._registry = MagicMock()  # noqa: SLF001
    job = SimpleNamespace(
        id="job-1",
        active_attempt_id=None,
        request_spec={},
        base_model="local-model",
        tenant_id="tenant-1",
        owner_subject="admin-1",
    )
    tenant_scope = hashlib.sha256(b"ananta.ml-intern-training.scope.v1\x00tenant-1\x00admin-1").hexdigest()
    result = {
        "_artifact_storage_binding": MlInternTrainingArtifactBinding(
            tenant_scope_digest=tenant_scope,
            job_id=job.id,
            attempt_id="worker-selected-attempt",
        ).to_mapping(),
    }

    with pytest.raises(ValueError, match="authoritative active Hub attempt"):
        publisher.publish(job, result)

    publisher._registry.register_trained.assert_not_called()  # noqa: SLF001


def test_worker_binding_must_match_authoritative_hub_attempt(tmp_path) -> None:
    publisher = RegistryTrainingResultPublisher(
        artifact_root=tmp_path,
        registry_path=tmp_path / "registry.json",
    )
    publisher._registry = MagicMock()  # noqa: SLF001
    job = SimpleNamespace(
        id="job-1",
        active_attempt_id="hub-attempt-1",
        request_spec={},
        base_model="local-model",
        tenant_id="tenant-1",
        owner_subject="admin-1",
    )
    tenant_scope = hashlib.sha256(b"ananta.ml-intern-training.scope.v1\x00tenant-1\x00admin-1").hexdigest()
    result = {
        "_artifact_storage_binding": MlInternTrainingArtifactBinding(
            tenant_scope_digest=tenant_scope,
            job_id=job.id,
            attempt_id="worker-attempt-2",
        ).to_mapping(),
    }

    with pytest.raises(ValueError, match="active Hub attempt"):
        publisher.publish(job, result)

    publisher._registry.register_trained.assert_not_called()  # noqa: SLF001


def test_active_hub_attempt_cannot_downgrade_to_legacy_storage(tmp_path) -> None:
    publisher = RegistryTrainingResultPublisher(
        artifact_root=tmp_path,
        registry_path=tmp_path / "registry.json",
    )
    legacy_adapter = tmp_path / "jobs" / "job-1" / "adapter"
    legacy_adapter.mkdir(parents=True)
    job = SimpleNamespace(
        id="job-1",
        active_attempt_id="hub-attempt-1",
        request_spec={},
        base_model="local-model",
        tenant_id="tenant-1",
        owner_subject="admin-1",
    )

    with pytest.raises(ValueError, match="artifact does not exist"):
        publisher.publish(job, {})
