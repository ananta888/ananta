from types import SimpleNamespace
from unittest.mock import MagicMock

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
