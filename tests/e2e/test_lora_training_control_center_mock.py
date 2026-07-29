from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from pathlib import Path

from agent.db_models import MlInternTrainingJobDB
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_training_result_publisher import RegistryTrainingResultPublisher
from agent.services.ml_intern_training_worker_port import HttpMlInternTrainingWorkerPort
from worker.runtime.lora_training_app import create_app
from worker.training.backends.mock import MockTrainingBackend
from worker.training.runtime import RuntimeConfiguration, TrainingWorkerRuntime

TOKEN = "mock-e2e-lora-worker-token-at-least-24-characters"
ENDPOINT = "http://lora-training-worker:8095/internal/v1/lora-training"


class _FlaskResponse:
    def __init__(self, response) -> None:
        self._body = response.get_data()
        self._offset = 0
        self.headers = dict(response.headers)

    def read(self, count: int = -1) -> bytes:
        if count < 0:
            count = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + count]
        self._offset += len(chunk)
        return chunk


class _FlaskOpener:
    def __init__(self, client) -> None:
        self._client = client

    def open(self, request, timeout: float):
        del timeout
        split = urllib.parse.urlsplit(request.full_url)
        path = split.path + (f"?{split.query}" if split.query else "")
        response = self._client.open(
            path=path,
            method=request.method,
            headers=dict(request.header_items()),
            data=request.data,
        )
        return _FlaskResponse(response)


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.is_symlink()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(child.read_bytes()).hexdigest().encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def _write_jsonl(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def test_mock_training_and_existing_adapter_evaluation_cross_the_real_http_contract(tmp_path: Path) -> None:
    shared = tmp_path / "project-workspaces"
    models = tmp_path / "models"
    state = tmp_path / "worker-state"
    artifacts = tmp_path / "hub-artifacts"
    model = models / "local-base"
    for path in (shared, model, state, artifacts):
        path.mkdir(parents=True)
    (model / "config.json").write_text('{"model_type":"mock"}', encoding="utf-8")
    train = shared / "datasets/train.jsonl"
    validation = shared / "datasets/validation.jsonl"
    _write_jsonl(train, {"instruction": "train prompt", "output": "train answer"})
    _write_jsonl(validation, {"instruction": "validation prompt", "output": "validation answer"})

    runtime = TrainingWorkerRuntime(
        RuntimeConfiguration(
            state_root=state,
            workspace_root=shared,
            dataset_root=shared,
            model_root=models,
            resource_profile="mock",
            max_workers=1,
            max_queue=2,
            isolate_processes=False,
        ),
        {"mock": MockTrainingBackend()},
    )
    registry_path = artifacts / "adapter_registry.json"
    registry = MlInternAdapterRegistryService(registry_path)
    registry_scope = {"tenant_id": "tenant-e2e", "owner_subject": "admin-e2e"}
    scope_digest = hashlib.sha256(b"ananta.ml-intern-training.scope.v1\x00tenant-e2e\x00admin-e2e").hexdigest()

    def resolve_adapter(adapter_id: str, tenant_scope_digest: str) -> Path:
        assert tenant_scope_digest == scope_digest
        record = registry.get_by_scope_digest(adapter_id, tenant_scope_digest)
        assert record is not None
        return Path(record.artifact_paths["adapter_dir"])

    port = HttpMlInternTrainingWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token=TOKEN,
        dataset_root=shared,
        workspace_root=shared,
        artifact_root=artifacts,
        model_catalog={
            "local/base": {
                "relative_path": "local-base",
                "snapshot_hash": _tree_hash(model),
            }
        },
        adapter_resolver=resolve_adapter,
        admitted_backends=("mock",),
        resource_profile="mock",
        resolver=lambda _host, _port: ("10.42.0.9",),
        opener=_FlaskOpener(create_app(runtime=runtime, auth_token=TOKEN).test_client()),
        poll_interval_seconds=0.05,
        sleeper=time.sleep,
    )
    publisher = RegistryTrainingResultPublisher(artifact_root=artifacts, registry_path=registry_path)
    events: list[dict] = []
    training_spec = {
        "dataset_id": "dataset-e2e",
        "dataset_version": "v1",
        "job_type": "train_lora",
        "mode": "live",
        "backend": "mock",
        "base_model": "local/base",
        "method": "lora",
        "_tenant_scope_digest": scope_digest,
        "output_name": "mock-e2e-adapter",
        "hyperparameters": {
            "max_steps": 2,
            "evaluation_steps": 1,
            "seed": 7,
            "batch_size": 1,
            "max_seq_length": 256,
        },
    }
    try:
        training = port.execute(
            job_id="lora-job-e2e-train",
            spec=training_spec,
            dataset_path=train,
            validation_path=validation,
            attempt_id="lora-attempt-e2e-train",
            fencing_token=11,
            on_event=lambda event: events.append(dict(event)),
            cancel_check=lambda: False,
        )
        training_job = MlInternTrainingJobDB(
            id="lora-job-e2e-train",
            tenant_id="tenant-e2e",
            owner_subject="admin-e2e",
            task_id="task-e2e-train",
            dataset_id=None,
            job_type="train_lora",
            mode="live",
            backend="mock",
            base_model="local/base",
            idempotency_key_digest="a" * 64,
            request_digest="b" * 64,
            request_spec=training_spec,
            active_attempt_id="lora-attempt-e2e-train",
        )
        adapter_id = publisher.publish(training_job, training)
        trained_record = registry.get(adapter_id, **registry_scope)

        assert training["status"] == "completed"
        assert trained_record is not None and trained_record.status == "trained"
        assert trained_record.eval_score is None
        assert any(event.get("type") == "progress" for event in events)
        assert (Path(trained_record.artifact_paths["adapter_dir"]) / "adapter_model.safetensors").is_file()

        evaluation_spec = {
            "dataset_id": "dataset-e2e",
            "dataset_version": "v1",
            "job_type": "evaluate_lora",
            "mode": "live",
            "backend": "mock",
            "base_model": "local/base",
            "method": "lora",
            "_tenant_scope_digest": scope_digest,
            "adapter_id": adapter_id,
            "hyperparameters": {"seed": 7, "batch_size": 1, "max_seq_length": 256},
        }
        evaluation = port.execute(
            job_id="lora-job-e2e-eval",
            spec=evaluation_spec,
            dataset_path=train,
            validation_path=validation,
            attempt_id="lora-attempt-e2e-eval",
            fencing_token=12,
            on_event=lambda event: events.append(dict(event)),
            cancel_check=lambda: False,
        )
        evaluation_job = MlInternTrainingJobDB(
            id="lora-job-e2e-eval",
            tenant_id="tenant-e2e",
            owner_subject="admin-e2e",
            task_id="task-e2e-eval",
            dataset_id=None,
            job_type="evaluate_lora",
            mode="live",
            backend="mock",
            base_model="local/base",
            idempotency_key_digest="c" * 64,
            request_digest="d" * 64,
            request_spec=evaluation_spec,
            active_attempt_id="lora-attempt-e2e-eval",
        )
        publisher.publish_evaluation(evaluation_job, evaluation)
        evaluated_record = registry.get(adapter_id, **registry_scope)

        assert evaluation["metrics"]["base"]["eval_loss"] == 1.0
        assert evaluation["metrics"]["adapter"]["eval_loss"] == 0.75
        assert evaluated_record is not None
        assert evaluated_record.eval_report_ref == "lora-job-e2e-eval"
        assert evaluated_record.eval_score == 0.25
        evaluation_manifest = (
            artifacts
            / "tenants"
            / scope_digest
            / "jobs"
            / "lora-job-e2e-eval"
            / "attempts"
            / "lora-attempt-e2e-eval"
            / "artifacts"
            / "evaluation_manifest.json"
        )
        assert evaluation_manifest.is_file()
        assert not (artifacts / "jobs/lora-job-e2e-eval").exists()
    finally:
        runtime.close()
