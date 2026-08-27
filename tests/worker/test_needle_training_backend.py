from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worker.training.backends.base import TrainingBackendError, TrainingContext, run_backend
from worker.training.backends.needle import NeedleTrainingBackend
from worker.training.contracts import (
    CONTRACT_VERSION,
    BaseModelSpec,
    DatasetManifest,
    SplitManifest,
    TrainingConfiguration,
    TrainingJobRequest,
)
from worker.training.datasets import VerifiedDataset
from worker.training.process_control import CancellationToken


class _CompletedProcess:
    returncode = 0

    def poll(self):
        return 0


class _ProcessController:
    def __init__(self) -> None:
        self.command = []
        self.environment = {}

    def start(self, command, *, cwd, env, stdout, stderr):
        del cwd, stdout, stderr
        self.command = list(command)
        self.environment = dict(env)
        output = Path(self.command[self.command.index("--out") + 1])
        output.write_bytes(b"needle-adapter")
        return _CompletedProcess()


def _context(tmp_path: Path) -> TrainingContext:
    train = tmp_path / "train.jsonl"
    validation = tmp_path / "validation.jsonl"
    train.write_text('{"instruction":"a","output":"b"}\n', encoding="utf-8")
    validation.write_text('{"instruction":"c","output":"d"}\n', encoding="utf-8")
    train_split = SplitManifest("train.jsonl", hashlib.sha256(train.read_bytes()).hexdigest(), 1)
    validation_split = SplitManifest("validation.jsonl", hashlib.sha256(validation.read_bytes()).hexdigest(), 1)
    dataset = DatasetManifest("dataset-1", "v1", train_split, validation_split)
    checkpoint = tmp_path / "needle-base.ckpt"
    checkpoint.write_bytes(b"base")
    configuration = TrainingConfiguration(
        seed=42,
        max_steps=10,
        num_train_epochs=3,
        learning_rate=0.0001,
        train_batch_size=8,
        eval_batch_size=8,
        gradient_accumulation_steps=1,
        eval_steps=1,
        save_steps=1,
        early_stopping_patience=3,
        lora_rank=8,
        lora_alpha=16,
        lora_dropout=0.0,
        max_sequence_length=256,
        quantization="none",
        gradient_checkpointing=False,
        target_modules=("q_proj",),
    )
    request = TrainingJobRequest(
        contract_version=CONTRACT_VERSION,
        job_id="job-needle",
        attempt_id="attempt-1",
        fencing_token=1,
        correlation_id="correlation-1",
        job_type="train_lora",
        backend="needle",
        resource_profile="cpu",
        tenant_scope_digest="a" * 64,
        workspace_ref="workspace-1",
        deadline_epoch_ms=2**62,
        base_model=BaseModelSpec("needle-2", "needle-base.ckpt", "b" * 64),
        dataset=dataset,
        configuration=configuration,
    )
    return TrainingContext(
        request=request,
        dataset=VerifiedDataset(train, validation, 1, 1, dataset.identity_hash),
        model_path=checkpoint,
        artifact_root=tmp_path / "artifacts",
        checkpoint_root=tmp_path / "checkpoints",
        resume_path=None,
        cancel=CancellationToken(),
        emit=lambda *_: None,
    )


def test_needle_backend_is_cpu_only_bounded_and_disables_generation(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "needle"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("ANANTA_NEEDLE_TRAINING_BIN", str(binary))
    monkeypatch.setenv("ANANTA_NEEDLE_TRAINING_CPU_SET", "2-4")
    real_which = __import__("shutil").which
    monkeypatch.setattr(
        "worker.training.backends.needle.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"nice", "taskset"} else real_which(name),
    )
    processes = _ProcessController()

    outcome = run_backend(NeedleTrainingBackend(process_controller=processes), _context(tmp_path))

    assert processes.command[:6] == ["/usr/bin/nice", "-n", "15", "/usr/bin/taskset", "--cpu-list", "2-4"]
    assert processes.command[processes.command.index("--max-len") + 1] == "256"
    assert processes.command[processes.command.index("--generate") + 1] == "0"
    assert processes.environment["JAX_PLATFORMS"] == "cpu"
    assert processes.environment["HF_HUB_OFFLINE"] == "1"
    assert {path.name for path in outcome.artifacts} == {"adapter.pkl", "evaluation.json"}
    assert json.loads((tmp_path / "artifacts/evaluation.json").read_text())["independent_evaluation_required"] is True


def test_needle_backend_rejects_more_than_four_cores(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "needle"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("ANANTA_NEEDLE_TRAINING_BIN", str(binary))
    monkeypatch.setenv("ANANTA_NEEDLE_TRAINING_CPU_SET", "0-7")
    monkeypatch.setattr("worker.training.backends.needle.shutil.which", lambda name: f"/usr/bin/{name}")

    with pytest.raises(TrainingBackendError, match="CPU set is invalid"):
        NeedleTrainingBackend().prepare(_context(tmp_path))
