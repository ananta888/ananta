from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worker.training.backend_config_compiler import BackendConfigCompiler, canonical_json
from worker.training.backend_config_policy import BackendConfigPolicy
from worker.training.backends.base import TrainingBackendError, TrainingContext
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


def _context(tmp_path: Path, backend: str) -> TrainingContext:
    model = tmp_path / "models" / "base"
    model.mkdir(parents=True)
    dataset_root = tmp_path / "datasets"
    dataset_root.mkdir()
    train = dataset_root / "train.jsonl"
    validation = dataset_root / "validation.jsonl"
    train.write_text('{"text":"train"}\n', encoding="utf-8")
    validation.write_text('{"text":"validation"}\n', encoding="utf-8")

    def split(path: Path) -> SplitManifest:
        return SplitManifest(
            relative_path=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            record_count=1,
        )

    dataset = DatasetManifest(
        dataset_id="dataset-1",
        dataset_version="v1",
        train=split(train),
        validation=split(validation),
    )
    request = TrainingJobRequest(
        contract_version=CONTRACT_VERSION,
        job_id="job-1",
        attempt_id="attempt-1",
        fencing_token=1,
        correlation_id="correlation-1",
        job_type="train_lora",
        backend=backend,
        resource_profile="nvidia",
        tenant_scope_digest="a" * 64,
        workspace_ref="workspace-1",
        deadline_epoch_ms=2**62,
        base_model=BaseModelSpec(model_id="local/base", relative_path="base", snapshot_hash="b" * 64),
        dataset=dataset,
        configuration=TrainingConfiguration(
            seed=17,
            max_steps=12,
            num_train_epochs=2.0,
            learning_rate=0.0002,
            train_batch_size=1,
            eval_batch_size=1,
            gradient_accumulation_steps=4,
            eval_steps=2,
            save_steps=4,
            early_stopping_patience=2,
            lora_rank=16,
            lora_alpha=32,
            lora_dropout=0.05,
            max_sequence_length=512,
            quantization="4bit",
            gradient_checkpointing=True,
            target_modules=("q_proj", "v_proj"),
        ),
    )
    return TrainingContext(
        request=request,
        dataset=VerifiedDataset(
            train_path=train,
            validation_path=validation,
            train_records=1,
            validation_records=1,
            dataset_hash=dataset.identity_hash,
        ),
        model_path=model,
        artifact_root=tmp_path / "artifacts",
        checkpoint_root=tmp_path / "checkpoints",
        resume_path=None,
        cancel=CancellationToken(),
        emit=lambda _event, _payload: None,
    )


@pytest.mark.parametrize("backend", ["axolotl", "llamafactory", "autotrain", "torchtune"])
def test_compiler_is_deterministic_offline_and_secret_free(tmp_path: Path, backend: str) -> None:
    context = _context(tmp_path, backend)
    first = BackendConfigCompiler().compile(backend, context)
    second = BackendConfigCompiler().compile(backend, context)
    assert first == second
    assert first.sha256 == hashlib.sha256(canonical_json(first.values).encode()).hexdigest()
    rendered = canonical_json(first.values)
    assert "https://" not in rendered
    assert "trust_remote_code" not in rendered
    assert "hub_token" not in rendered.lower()


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "python evil.py"},
        {"path": "/etc/passwd"},
        {"path": "../../escape"},
        {"path": "https://attacker.invalid/data"},
        {"plugin": "attacker.module"},
        {"push_to_hub": True},
        {"report_to": "wandb"},
        {"value": "$(touch owned)"},
        {"value": "secret\nleak"},
    ],
)
def test_policy_blocks_injection_paths_egress_plugins_and_secrets(tmp_path: Path, payload: dict[str, object]) -> None:
    with pytest.raises(TrainingBackendError) as error:
        BackendConfigPolicy(allowed_roots=(tmp_path,)).validate(payload)
    assert error.value.code == "config_invalid"


def test_compiled_config_write_is_canonical(tmp_path: Path) -> None:
    compiled = BackendConfigCompiler().compile("axolotl", _context(tmp_path, "axolotl"))
    path = compiled.write(tmp_path / "config" / "axolotl.yaml")
    assert json.loads(path.read_text()) == compiled.values
    assert path.read_text().endswith("\n")


@pytest.mark.parametrize("backend", ["axolotl", "llamafactory", "autotrain", "torchtune"])
def test_resume_is_derived_only_from_the_admitted_checkpoint(tmp_path: Path, backend: str) -> None:
    context = _context(tmp_path, backend)
    resume = context.checkpoint_root / "checkpoint-4"
    resume.mkdir(parents=True)
    resumed = TrainingContext(
        request=context.request,
        dataset=context.dataset,
        model_path=context.model_path,
        artifact_root=context.artifact_root,
        checkpoint_root=context.checkpoint_root,
        resume_path=resume,
        cancel=context.cancel,
        emit=context.emit,
    )

    rendered = canonical_json(BackendConfigCompiler().compile(backend, resumed).values)

    assert str(resume.resolve()) in rendered
