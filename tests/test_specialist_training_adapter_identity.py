"""Real artifact inspection/registry with synthetic model files and Hub job metadata."""

from types import SimpleNamespace

import pytest

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_training_artifact_binding import MlInternTrainingArtifactBinding
from agent.services.ml_intern_training_result_publisher import RegistryTrainingResultPublisher
from agent.services.unsloth_storage_governance_service import tenant_scope_digest
from tests.test_ml_intern_training_control_service import _principal
from tests.test_ml_intern_training_worker_port import _safetensors_bytes


@pytest.mark.parametrize("supplied", [None, "adapter-synthetic-job"])
def test_specialist_registry_identity_is_derived_from_hub_job(tmp_path, supplied):
    principal = _principal()
    job = SimpleNamespace(
        id="lora-job-synthetic-job",
        active_attempt_id="synthetic-attempt",
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        base_model="local/base",
        backend="mock",
        dataset_id="synthetic-dataset",
        request_spec={
            "task_family": "specialist_decision",
            "method": "lora",
            "task_kinds": ["specialist_decision"],
            "dataset_hash": "a" * 64,
        },
    )
    binding = MlInternTrainingArtifactBinding(tenant_scope_digest(principal), job.id, job.active_attempt_id)
    directory = tmp_path / binding.relative_directory("adapter")
    directory.mkdir(parents=True)
    (directory / "adapter_config.json").write_text('{"base_model_name_or_path":"local/base"}')
    (directory / "adapter_model.safetensors").write_bytes(_safetensors_bytes())
    publisher = RegistryTrainingResultPublisher(artifact_root=tmp_path, registry_path=tmp_path / "registry.json")
    result = {"_artifact_storage_binding": binding.to_mapping()}
    if supplied is not None:
        result["adapter_id"] = supplied
    identifier = publisher.publish(job, result)
    assert identifier == "adapter-synthetic-job"
    record = MlInternAdapterRegistryService(tmp_path / "registry.json").get(
        identifier, tenant_id=principal.tenant_id, owner_subject=principal.subject
    )
    assert record.status == "trained"
    assert record.artifact_paths["adapter_dir"] == str(directory)
    assert len(record.artifact_sha256) == 64 and not record.provenance_verified


def test_worker_cannot_publish_specialist_bytes_under_another_job_identity(tmp_path):
    publisher = RegistryTrainingResultPublisher(artifact_root=tmp_path, registry_path=tmp_path / "registry.json")
    job = SimpleNamespace(id="lora-job-synthetic-job", request_spec={"task_family": "specialist_decision"})
    with pytest.raises(ValueError, match="does not match the Hub job"):
        publisher.publish(job, {"adapter_id": "adapter-another-job"})
    assert not (tmp_path / "registry.json").exists()
