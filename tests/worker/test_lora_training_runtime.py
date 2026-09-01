from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from worker.training.backends.base import TrainingBackendError, TrainingContext
from worker.training.backends.mock import MockTrainingBackend
from worker.training.contracts import CONTRACT_VERSION, TrainingContractError
from worker.training.process_control import TrainingCancelled
from worker.training.runtime import RuntimeConfiguration, TrainingRuntimeError, TrainingWorkerRuntime


def _write_split(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    data = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"relative_path": path.name, "sha256": hashlib.sha256(data).hexdigest(), "record_count": len(rows)}


def _path_digest(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(child.read_bytes()).hexdigest().encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def _governance(base_model_digest: str) -> dict[str, str]:
    bindings = {
        "training_profile_digest": "1" * 64,
        "base_model_digest": base_model_digest,
        "dataset_manifest_digest": "2" * 64,
        "dataset_artifact_digest": "3" * 64,
        "dataset_recipe_digest": "4" * 64,
        "split_lock_digest": "5" * 64,
        "action_schema_digest": "6" * 64,
        "serializer_digest": "7" * 64,
        "policy_digest": "8" * 64,
        "resource_profile_digest": "9" * 64,
        "training_admission_digest": "a" * 64,
    }
    encoded = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()
    return {**bindings, "governance_digest": hashlib.sha256(encoded).hexdigest()}


def _setup(
    tmp_path: Path,
    backend: Any | None = None,
    *,
    max_workers: int = 1,
    max_queue: int = 2,
    isolate_processes: bool = False,
):
    state = tmp_path / "state"
    workspaces = tmp_path / "workspaces"
    datasets = tmp_path / "datasets"
    models = tmp_path / "models"
    for path in (state, workspaces / "project-1", datasets, models / "base-model"):
        path.mkdir(parents=True)
    (models / "base-model" / "config.json").write_text('{"model_type":"mock"}', encoding="utf-8")
    train = _write_split(datasets / "train.jsonl", [{"instruction": "alpha", "output": "one"}])
    validation = _write_split(datasets / "validation.jsonl", [{"instruction": "beta", "output": "two"}])
    runtime = TrainingWorkerRuntime(
        RuntimeConfiguration(
            state_root=state,
            workspace_root=workspaces,
            dataset_root=datasets,
            model_root=models,
            resource_profile="mock",
            max_workers=max_workers,
            max_queue=max_queue,
            isolate_processes=isolate_processes,
        ),
        {"mock": backend or MockTrainingBackend()},
    )
    request = {
        "contract_version": CONTRACT_VERSION,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "job_type": "train_lora",
        "backend": "mock",
        "tenant_scope_digest": "a" * 64,
        "workspace_ref": "project-1",
        "deadline_epoch_ms": int(time.time() * 1000) + 60_000,
        "base_model": {
            "model_id": "local/base-model",
            "relative_path": "base-model",
            "snapshot_hash": _path_digest(models / "base-model"),
        },
        "dataset": {
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "train": train,
            "validation": validation,
        },
        "configuration": {
            "seed": 7,
            "max_steps": 3,
            "num_train_epochs": 1,
            "learning_rate": 0.0002,
            "train_batch_size": 1,
            "eval_batch_size": 1,
            "gradient_accumulation_steps": 1,
            "eval_steps": 1,
            "save_steps": 1,
            "early_stopping_patience": 3,
            "lora_rank": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.05,
            "max_sequence_length": 256,
            "quantization": "none",
            "gradient_checkpointing": True,
            "target_modules": ["q_proj", "v_proj"],
        },
    }
    return runtime, request, state


def _terminal(runtime: TrainingWorkerRuntime, job_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runtime.status(job_id)
        if status["status"] in {"succeeded", "failed", "cancelled"}:
            return status
        time.sleep(0.01)
    raise AssertionError(f"job did not terminate: {runtime.status(job_id)}")


def _attempt_root(
    state: Path,
    request: Mapping[str, Any],
) -> Path:
    return (
        state
        / "tenants"
        / str(request["tenant_scope_digest"])
        / "jobs"
        / str(request["job_id"])
        / "attempts"
        / str(request["attempt_id"])
    )


def test_worker_contract_rejects_missing_tenant_scope_binding(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    request.pop("tenant_scope_digest")
    try:
        with pytest.raises(TrainingContractError) as error:
            runtime.submit(request)
        assert error.value.code == "invalid_hash"
    finally:
        runtime.close()


def test_worker_contract_rejects_unknown_orchestration_fields(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    request["worker_url"] = "http://attacker.invalid"
    try:
        with pytest.raises(TrainingContractError) as error:
            runtime.submit(request)
        assert error.value.code == "invalid_contract_shape"
    finally:
        runtime.close()


def _evaluation_request(training_request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    adapter = workspace_root / "project-1" / "adapter-1"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"r":8}', encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": "evaluation-1",
        "attempt_id": "evaluation-attempt-1",
        "fencing_token": 1,
        "correlation_id": "evaluation-correlation-1",
        "job_type": "evaluate_existing_adapter",
        "backend": "mock",
        "tenant_scope_digest": training_request["tenant_scope_digest"],
        "workspace_ref": "project-1",
        "deadline_epoch_ms": int(time.time() * 1000) + 60_000,
        "base_model": training_request["base_model"],
        "adapter": {
            "adapter_id": "adapter-1",
            "relative_path": "adapter-1",
            "sha256": _path_digest(adapter),
        },
        "validation_dataset": {
            "dataset_id": training_request["dataset"]["dataset_id"],
            "dataset_version": training_request["dataset"]["dataset_version"],
            "validation": training_request["dataset"]["validation"],
        },
        "configuration": {
            "seed": 7,
            "batch_size": 1,
            "max_sequence_length": 256,
            "max_samples": 100,
            "quantization": "none",
            "scorer_name": "ananta_todo_json",
        },
    }


def test_mock_job_is_async_persistent_and_produces_verified_artifacts(tmp_path: Path) -> None:
    runtime, request, state = _setup(tmp_path)
    governance = _governance(str(request["base_model"]["snapshot_hash"]))
    request["governance"] = governance
    try:
        accepted = runtime.submit(request)
        result = _terminal(runtime, request["job_id"])

        assert accepted["status"] in {"queued", "running", "succeeded"}
        assert result["status"] == "succeeded"
        assert result["correlation_id"] == "correlation-1"
        assert result["progress"]["step"] == 3
        assert {item["name"] for item in result["artifacts"]} >= {
            "adapter_config.json",
            "adapter_model.safetensors",
            "evaluation.json",
            "training_manifest.json",
        }
        manifest_path, metadata = runtime.artifact(request["job_id"], "training_manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert metadata["sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        assert manifest["configuration"]["seed"] == 7
        assert manifest["dataset"]["verified_validation_records"] == 1
        assert manifest["base_model"]["snapshot_hash"] == _path_digest(tmp_path / "models" / "base-model")
        assert manifest["governance"] == governance
        assert "instruction" not in json.dumps(manifest["governance"])
        assert (_attempt_root(state, request) / "status.json").is_file()

        event_page = runtime.events("job-1", after_sequence=0, limit=100)
        sequences = [event["sequence"] for event in event_page["events"]]
        assert sequences == sorted(sequences)
        assert all(event["correlation_id"] == "correlation-1" for event in event_page["events"])
        assert not any("instruction" in json.dumps(event) for event in event_page["events"])
    finally:
        runtime.close()


def test_mock_job_runs_in_isolated_process_without_inheriting_worker_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANANTA_LORA_TRAINING_TOKEN", "must-not-enter-training-child")
    runtime, request, state = _setup(tmp_path, isolate_processes=True)
    try:
        runtime.submit(request)
        result = _terminal(runtime, "job-1", timeout=10)

        assert result["status"] == "succeeded"
        assert (_attempt_root(state, request) / "process" / "result.json").is_file()
    finally:
        runtime.close()


def test_existing_adapter_evaluation_compares_base_and_adapter_without_training(tmp_path: Path) -> None:
    runtime, training_request, _state = _setup(tmp_path)
    evaluation = _evaluation_request(training_request, tmp_path / "workspaces")
    try:
        accepted = runtime.submit(evaluation)
        result = _terminal(runtime, "evaluation-1")

        assert accepted["job_type"] == "evaluate_existing_adapter"
        assert result["status"] == "succeeded"
        assert result["metrics"]["base"]["eval_loss"] == 1.0
        assert result["metrics"]["adapter"]["eval_loss"] == 0.75
        assert result["metrics"]["delta"]["eval_loss"] == -0.25
        assert result["metrics"]["scorer_name"] == "ananta_todo_json"
        assert result["metrics"]["samples"]
        assert "base_score" in result["metrics"]["samples"][0]
        assert "adapter_score" in result["metrics"]["samples"][0]
        assert {item["name"] for item in result["artifacts"]} == {
            "eval_report.json",
            "evaluation.json",
            "evaluation_manifest.json",
        }
        events = runtime.events("evaluation-1")["events"]
        assert {event["payload"].get("phase") for event in events if event["type"] == "phase"} == {
            "evaluating_base",
            "evaluating_adapter",
        }
        assert "alpha" not in json.dumps(events)
    finally:
        runtime.close()


def test_existing_adapter_evaluation_runs_in_isolated_process(tmp_path: Path) -> None:
    runtime, training_request, state = _setup(tmp_path, isolate_processes=True)
    evaluation = _evaluation_request(training_request, tmp_path / "workspaces")
    try:
        runtime.submit(evaluation)
        result = _terminal(runtime, "evaluation-1", timeout=10)

        assert result["status"] == "succeeded"
        result_path = _attempt_root(state, evaluation) / "process" / "result.json"
        assert result_path.is_file()
    finally:
        runtime.close()


def test_existing_adapter_evaluation_rejects_hash_mismatch_before_backend(tmp_path: Path) -> None:
    runtime, training_request, _state = _setup(tmp_path)
    evaluation = _evaluation_request(training_request, tmp_path / "workspaces")
    evaluation["adapter"] = {**evaluation["adapter"], "sha256": "0" * 64}
    try:
        runtime.submit(evaluation)
        result = _terminal(runtime, "evaluation-1")

        assert result["status"] == "failed"
        assert result["error"]["code"] == "adapter_hash_mismatch"
    finally:
        runtime.close()


def test_artifact_download_rechecks_integrity_and_rejects_traversal(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    try:
        runtime.submit(request)
        assert _terminal(runtime, "job-1")["status"] == "succeeded"
        path, _metadata = runtime.artifact("job-1", "adapter_config.json")
        path.write_text("tampered", encoding="utf-8")

        with pytest.raises(TrainingRuntimeError) as integrity:
            runtime.artifact("job-1", "adapter_config.json")
        with pytest.raises(TrainingRuntimeError) as traversal:
            runtime.artifact("job-1", "../status.json")

        assert integrity.value.code == "artifact_hash_mismatch"
        assert traversal.value.code == "artifact_not_found"
    finally:
        runtime.close()


def test_resume_checkpoint_is_source_job_and_hash_bound(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    try:
        runtime.submit(request)
        first = _terminal(runtime, "job-1")
        assert first["status"] == "succeeded"
        assert first["resume_checkpoint"] is not None

        resumed = {
            **request,
            "job_id": "job-2",
            "attempt_id": "attempt-2",
            "correlation_id": "correlation-2",
            "resume_checkpoint": first["resume_checkpoint"],
        }
        runtime.submit(resumed)
        second = _terminal(runtime, "job-2")

        assert second["status"] == "succeeded"
    finally:
        runtime.close()


def test_resume_checkpoint_rejects_configuration_drift(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    try:
        runtime.submit(request)
        first = _terminal(runtime, "job-1")
        changed = {
            **request,
            "job_id": "job-2",
            "attempt_id": "attempt-2",
            "correlation_id": "correlation-2",
            "configuration": {**request["configuration"], "seed": 8},
            "resume_checkpoint": first["resume_checkpoint"],
        }

        with pytest.raises(ValueError) as error:
            runtime.submit(changed)

        assert getattr(error.value, "code", None) == "checkpoint_binding_mismatch"
    finally:
        runtime.close()


class _BlockingBackend(MockTrainingBackend):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.contexts: list[TrainingContext] = []

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        self.contexts.append(context)
        self.started.set()
        while not self.release.wait(0.01):
            context.cancel.raise_if_cancelled()
        return super().train(context, prepared)


class _ForcedCancellationBackend(MockTrainingBackend):
    def __init__(self) -> None:
        self.started = threading.Event()

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        self.started.set()
        while not context.cancel.cancelled:
            time.sleep(0.01)
        raise TrainingCancelled("training cancellation required SIGKILL", forced=True)


class _CheckpointSupersessionBackend(MockTrainingBackend):
    def __init__(self) -> None:
        self.first_checkpoint = threading.Event()
        self.contexts: list[TrainingContext] = []
        self._lock = threading.Lock()

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        with self._lock:
            self.contexts.append(context)
            call_number = len(self.contexts)
        if call_number == 1:
            checkpoint = context.checkpoint_root / "checkpoint-1.json"
            checkpoint.write_text('{"step":1}', encoding="utf-8")
            context.emit("checkpoint", {"step": 1, "name": checkpoint.name})
            self.first_checkpoint.set()
            while True:
                context.cancel.raise_if_cancelled()
                time.sleep(0.01)
        assert context.resume_path is not None
        return super().train(context, prepared)


class _LateResultBackend(MockTrainingBackend):
    def __init__(self) -> None:
        self.old_save_blocked = threading.Event()
        self.release_old_save = threading.Event()
        self.old_result_returned = threading.Event()
        self.contexts: list[TrainingContext] = []
        self._lock = threading.Lock()

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        with self._lock:
            self.contexts.append(context)
        return super().prepare(context)

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ):
        outcome = super().save(context, prepared, trained, metrics)
        if context.request.attempt_id == "attempt-1":
            self.old_save_blocked.set()
            assert self.release_old_save.wait(3)
            self.old_result_returned.set()
        return outcome


def test_queue_capacity_is_bounded_and_returns_stable_reason(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend, max_workers=1, max_queue=1)
    try:
        runtime.submit(request)
        assert backend.started.wait(1)
        second = {**request, "job_id": "job-2", "attempt_id": "attempt-2", "correlation_id": "correlation-2"}
        third = {**request, "job_id": "job-3", "attempt_id": "attempt-3", "correlation_id": "correlation-3"}
        runtime.submit(second)

        with pytest.raises(TrainingRuntimeError) as error:
            runtime.submit(third)

        assert error.value.code == "queue_full"
        assert error.value.http_status == 429
    finally:
        backend.release.set()
        runtime.close()


def test_running_job_cancels_cooperatively_and_late_events_are_fenced(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    try:
        runtime.submit(request)
        assert backend.started.wait(1)
        cancelled = runtime.cancel("job-1")
        result = _terminal(runtime, "job-1")

        assert cancelled["status"] == "cancel_requested"
        assert result["status"] == "cancelled"
        assert result["cancel_mode"] == "graceful"
        assert runtime.cancel("job-1")["status"] == "cancelled"
    finally:
        backend.release.set()
        runtime.close()


def test_forced_process_containment_is_visible_in_terminal_status(tmp_path: Path) -> None:
    backend = _ForcedCancellationBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    try:
        runtime.submit(request)
        assert backend.started.wait(1)
        runtime.cancel("job-1")

        result = _terminal(runtime, "job-1")
        events = runtime.events("job-1")["events"]

        assert result["status"] == "cancelled"
        assert result["cancel_mode"] == "forced"
        assert events[-1]["payload"]["reason_code"] == "forced_cancel"
    finally:
        runtime.close()


def test_retry_advances_fence_preserves_inputs_and_rejects_old_attempt_events(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    try:
        runtime.submit(request)
        assert backend.started.wait(1)
        runtime.cancel("job-1")
        assert _terminal(runtime, "job-1")["status"] == "cancelled"
        old_context = backend.contexts[0]
        backend.release.set()
        retry = {
            **request,
            "attempt_id": "attempt-2",
            "fencing_token": 2,
            "correlation_id": "correlation-2",
            "deadline_epoch_ms": int(time.time() * 1000) + 60_000,
        }

        runtime.submit(retry)
        result = _terminal(runtime, "job-1")

        assert result["status"] == "succeeded"
        assert result["attempt_id"] == "attempt-2"
        with pytest.raises(TrainingBackendError) as stale:
            old_context.emit("progress", {"step": 1, "max_steps": 3})
        assert stale.value.code == "stale_fence"
    finally:
        backend.release.set()
        runtime.close()


def test_higher_fence_supersedes_active_attempt_and_resumes_its_checkpoint(tmp_path: Path) -> None:
    backend = _CheckpointSupersessionBackend()
    runtime, request, state = _setup(tmp_path, backend)
    try:
        runtime.submit(request)
        assert backend.first_checkpoint.wait(1)
        first_status = runtime.status("job-1")
        checkpoint = first_status["resume_checkpoint"]
        assert checkpoint is not None
        old_context = backend.contexts[0]
        stale_retry = {
            **request,
            "attempt_id": "attempt-stale",
            "correlation_id": "correlation-stale",
        }
        with pytest.raises(TrainingRuntimeError) as stale_admission:
            runtime.submit(stale_retry)
        assert stale_admission.value.code == "stale_fence"
        assert old_context.cancel.cancelled is False
        retry = {
            **request,
            "attempt_id": "attempt-2",
            "fencing_token": 2,
            "correlation_id": "correlation-2",
            "deadline_epoch_ms": int(time.time() * 1000) + 60_000,
            "resume_checkpoint": checkpoint,
        }

        runtime.submit(retry)
        result = _terminal(runtime, "job-1")

        assert old_context.cancel.cancelled is True
        assert result["status"] == "succeeded"
        assert result["attempt_id"] == "attempt-2"
        assert backend.contexts[1].resume_path == state / checkpoint["relative_path"]
        old_status = json.loads(
            (_attempt_root(state, request) / "status.json").read_text(
                encoding="utf-8"
            )
        )
        assert old_status["status"] == "cancelled"
        assert old_status["error"]["code"] == "superseded_by_higher_fence"
        with pytest.raises(TrainingBackendError) as stale:
            old_context.emit("progress", {"step": 1, "max_steps": 3})
        assert stale.value.code == "stale_fence"
    finally:
        runtime.close()


def test_late_result_from_superseded_attempt_cannot_replace_new_generation(tmp_path: Path) -> None:
    backend = _LateResultBackend()
    runtime, request, _state = _setup(tmp_path, backend, max_workers=2)
    try:
        runtime.submit(request)
        assert backend.old_save_blocked.wait(1)
        checkpoint = runtime.status("job-1")["resume_checkpoint"]
        assert checkpoint is not None
        retry = {
            **request,
            "attempt_id": "attempt-2",
            "fencing_token": 2,
            "correlation_id": "correlation-2",
            "deadline_epoch_ms": int(time.time() * 1000) + 60_000,
            "resume_checkpoint": checkpoint,
        }

        runtime.submit(retry)
        assert _terminal(runtime, "job-1")["attempt_id"] == "attempt-2"
        backend.release_old_save.set()
        assert backend.old_result_returned.wait(1)
        time.sleep(0.05)

        final = runtime.status("job-1")
        assert final["status"] == "succeeded"
        assert final["attempt_id"] == "attempt-2"
        manifest_path, _metadata = runtime.artifact("job-1", "training_manifest.json")
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["attempt_id"] == "attempt-2"
    finally:
        backend.release_old_save.set()
        runtime.close()


def test_retry_rejects_stale_fence_and_immutable_input_drift(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    try:
        runtime.submit(request)
        assert backend.started.wait(1)
        runtime.cancel("job-1")
        assert _terminal(runtime, "job-1")["status"] == "cancelled"
        stale_request = {**request, "attempt_id": "attempt-2", "correlation_id": "correlation-2"}
        drifted_request = {
            **request,
            "attempt_id": "attempt-2",
            "fencing_token": 2,
            "correlation_id": "correlation-2",
            "configuration": {**request["configuration"], "seed": 99},
        }

        with pytest.raises(TrainingRuntimeError) as stale:
            runtime.submit(stale_request)
        with pytest.raises(TrainingRuntimeError) as drift:
            runtime.submit(drifted_request)

        assert stale.value.code == "stale_fence"
        assert drift.value.code == "job_conflict"
    finally:
        backend.release.set()
        runtime.close()


def test_invalid_dataset_fails_before_backend_model_loading(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    request["dataset"] = {
        **request["dataset"],
        "validation": {**request["dataset"]["validation"], "sha256": "0" * 64},
    }
    try:
        runtime.submit(request)
        result = _terminal(runtime, "job-1")

        assert result["status"] == "failed"
        assert result["error"]["code"] == "dataset_hash_mismatch"
        assert not backend.started.is_set()
    finally:
        backend.release.set()
        runtime.close()


def test_base_model_snapshot_hash_is_verified_before_backend_loading(tmp_path: Path) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    request["base_model"] = {**request["base_model"], "snapshot_hash": "0" * 64}
    try:
        runtime.submit(request)
        result = _terminal(runtime, "job-1")

        assert result["status"] == "failed"
        assert result["error"]["code"] == "base_model_hash_mismatch"
        assert not backend.started.is_set()
    finally:
        backend.release.set()
        runtime.close()


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_unsafe_base_model_tree_fails_before_backend_loading(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    backend = _BlockingBackend()
    runtime, request, _state = _setup(tmp_path, backend)
    model = tmp_path / "models" / "base-model"
    if unsafe_kind == "symlink":
        (model / "blob-a").write_bytes(b"first")
        (model / "blob-b").write_bytes(b"second")
        request["base_model"] = {
            **request["base_model"],
            "snapshot_hash": _path_digest(model),
        }
        (model / "model.safetensors").symlink_to("blob-a")
    else:
        request["base_model"] = {
            **request["base_model"],
            "snapshot_hash": _path_digest(model),
        }
        os.mkfifo(model / "model.safetensors")
    try:
        runtime.submit(request)
        result = _terminal(runtime, "job-1")

        assert result["status"] == "failed"
        assert result["error"]["code"] == "invalid_path"
        assert not backend.started.is_set()
    finally:
        backend.release.set()
        runtime.close()


def test_degraded_runtime_rejects_submission(tmp_path: Path) -> None:
    runtime = TrainingWorkerRuntime(
        RuntimeConfiguration(
            state_root=tmp_path / "missing-state",
            workspace_root=tmp_path / "missing-workspace",
            dataset_root=tmp_path / "missing-datasets",
            model_root=tmp_path / "missing-models",
        ),
        {"mock": MockTrainingBackend()},
    )
    try:
        assert runtime.health()["status"] == "degraded"
        with pytest.raises(TrainingRuntimeError) as error:
            runtime.submit({})
        assert error.value.code == "worker_degraded"
    finally:
        runtime.close()


def test_submit_rejects_unknown_job_type_backend_and_expired_deadline(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    try:
        with pytest.raises(ValueError) as unknown_type:
            runtime.submit({**request, "job_type": "unknown_job"})
        with pytest.raises(TrainingRuntimeError) as unknown_backend:
            runtime.submit({**request, "backend": "unknown_backend"})
        with pytest.raises(TrainingRuntimeError) as expired:
            runtime.submit({**request, "deadline_epoch_ms": 1})

        assert getattr(unknown_type.value, "code", None) == "unsupported_job_type"
        assert unknown_backend.value.code == "backend_unavailable"
        assert expired.value.code == "deadline_expired"
    finally:
        runtime.close()


def test_submit_rejects_resource_profile_mismatch(tmp_path: Path) -> None:
    runtime, request, _state = _setup(tmp_path)
    try:
        with pytest.raises(TrainingRuntimeError) as mismatch:
            runtime.submit({**request, "resource_profile": "nvidia"})
        assert mismatch.value.code == "resource_profile_mismatch"
    finally:
        runtime.close()


def test_missing_workspace_and_model_fail_before_backend_loading(tmp_path: Path) -> None:
    workspace_backend = _BlockingBackend()
    workspace_runtime, workspace_request, _state = _setup(tmp_path / "workspace", workspace_backend)
    workspace_request["workspace_ref"] = "missing-workspace"
    try:
        workspace_runtime.submit(workspace_request)
        workspace_result = _terminal(workspace_runtime, "job-1")
        assert workspace_result["error"]["code"] == "workspace_missing"
        assert not workspace_backend.started.is_set()
    finally:
        workspace_backend.release.set()
        workspace_runtime.close()

    model_backend = _BlockingBackend()
    model_runtime, model_request, _state = _setup(tmp_path / "model", model_backend)
    model_request["base_model"] = {**model_request["base_model"], "relative_path": "missing-model"}
    try:
        model_runtime.submit(model_request)
        model_result = _terminal(model_runtime, "job-1")
        assert model_result["error"]["code"] == "model_missing"
        assert not model_backend.started.is_set()
    finally:
        model_backend.release.set()
        model_runtime.close()
