"""Technical outcome projection with real SQL/registry and synthetic model artifacts."""

import hashlib
import json

import pytest

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_training_artifact_binding import MlInternTrainingArtifactBinding
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.ml_intern_training_result_publisher import RegistryTrainingResultPublisher
from agent.services.specialist_dataset_binding import BINDING_KEY
from agent.services.specialist_prediction_storage import ConfinedSpecialistPredictionStorage
from agent.services.specialist_training_outcome import SpecialistTrainingOutcomeService
from agent.services.unsloth_storage_governance_service import tenant_scope_digest
from tests.test_ml_intern_training_control_service import InlineExecutor, _principal
from tests.test_ml_intern_training_worker_port import _safetensors_bytes
from tests.test_specialist_training_submission import prepared as _prepared


@pytest.fixture
def completed(app, tmp_path, monkeypatch):
    repo, principal, command, _queue, root = _prepared.__wrapped__(app, tmp_path, monkeypatch)
    monkeypatch.setattr("agent.services.ml_intern_training_control_execution.secrets.randbits", lambda bits: 17)
    artifacts = root / "artifacts"
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(artifacts)}, repository=repo, executor=InlineExecutor()
    )
    accepted, _ = control.create_job(principal, command.request_spec, idempotency_key="synthetic-outcome")
    job = repo.get_job(principal, accepted["id"])
    attempt = repo.get_attempt(job.active_attempt_id)
    storage = MlInternTrainingArtifactBinding(tenant_scope_digest(principal), job.id, attempt.id)
    adapter = artifacts / storage.relative_directory("adapter")
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": job.base_model}))
    (adapter / "adapter_model.safetensors").write_bytes(_safetensors_bytes())
    registry_path = root / "registry.json"
    publisher = RegistryTrainingResultPublisher(artifact_root=artifacts, registry_path=registry_path)
    job.adapter_id = publisher.publish(job, {"_artifact_storage_binding": storage.to_mapping()})
    pin = job.request_spec[BINDING_KEY]
    manifest = {
        "schema_version": "ananta.lora-training-manifest.v1",
        "contract_version": "ananta.lora-training.v1",
        "job_id": job.id,
        "attempt_id": attempt.id,
        "fencing_token": (attempt.attempt_number << 128) | 17,
        "backend": job.backend,
        "base_model": {"model_id": job.base_model, "snapshot_hash": "b" * 64},
        "dataset": {
            "dataset_id": job.dataset_id,
            **{
                name: {"sha256": pin[f"{name}_sha256"], "record_count": pin[f"{name}_records"]}
                for name in ("train", "validation")
            },
        },
    }
    manifest_path = artifacts / storage.relative_directory("artifacts") / "training_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    raw = json.dumps(manifest).encode()
    manifest_path.write_bytes(raw)
    job.result_summary = {
        "artifacts": [
            {"name": "training_manifest.json", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        ]
    }
    job = repo.save_job(job, expected_version=job.version)
    files = ConfinedSpecialistPredictionStorage(artifact_root=artifacts, dataset_root=root / "execution")
    reader = SpecialistTrainingOutcomeService(
        repository=repo,
        registry=MlInternAdapterRegistryService(registry_path),
        artifact_root=artifacts,
        model_catalog={job.base_model: {"snapshot_hash": "b" * 64}},
        open_artifact=files.open_artifact,
    )
    return reader, principal, job, command, manifest_path, adapter, repo


def test_scoped_completed_job_projects_verified_bytes_and_unknown_resources(completed):
    reader, principal, job, command, _manifest, _adapter, _repo = completed
    outcome = reader.read(principal, job.id, command)
    assert outcome.job_id == job.id and outcome.adapter_id == job.adapter_id
    assert outcome.base_model_digest == "b" * 64 and len(outcome.adapter_digest) == 64
    assert outcome.usage.adapter_bytes > 0
    assert outcome.usage.train_seconds is None and outcome.usage.parameter_count is None


def test_completed_training_remains_readable_after_regular_registry_evaluation(completed):
    reader, principal, job, command, _manifest, _adapter, _repo = completed
    before = reader.read(principal, job.id, command)
    record = reader._registry.get(job.adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
    evaluated = reader._registry.set_eval_report(
        job.adapter_id, eval_report_ref="synthetic-validation-job", eval_score=0.9,
        tenant_id=principal.tenant_id, owner_subject=principal.subject, expected_version=record.registry_version,
    )
    assert evaluated.status == "evaluated" and evaluated.approved_by is None
    assert reader.read(principal, job.id, command) == before
    reader._registry.reject(
        job.adapter_id, reason="synthetic rejection", tenant_id=principal.tenant_id,
        owner_subject=principal.subject, expected_version=evaluated.registry_version,
    )
    with pytest.raises(ValueError, match="specialist_training_registry_binding_invalid"):
        reader.read(principal, job.id, command)


@pytest.mark.parametrize("mutation", ["manifest", "manifest_binding", "adapter", "dataset_version", "foreign_owner"])
def test_mutated_training_result_or_authority_is_not_an_outcome(completed, mutation):
    reader, principal, job, command, manifest, adapter, repo = completed
    if mutation == "manifest":
        manifest.write_bytes(manifest.read_bytes().replace(b"bbbb", b"cccc", 1))
    elif mutation == "manifest_binding":
        changed = json.loads(manifest.read_bytes())
        changed["fencing_token"] += 1
        raw = json.dumps(changed).encode()
        manifest.write_bytes(raw)
        job.result_summary["artifacts"][0].update({"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)})
        repo.save_job(job, expected_version=job.version)
    elif mutation == "adapter":
        (adapter / "adapter_config.json").write_text('{"base_model_name_or_path":"changed"}')
    elif mutation == "dataset_version":
        dataset = repo.get_dataset(principal, job.dataset_id)
        dataset.name = "changed"
        repo.save_dataset(dataset, expected_version=dataset.version)
    else:
        principal = _principal()
    with pytest.raises(ValueError):
        reader.read(principal, job.id, command)
