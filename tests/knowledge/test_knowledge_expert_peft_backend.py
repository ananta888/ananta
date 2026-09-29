from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from worker.training.backends.base import TrainingContext, TrainingOutcome
from worker.training.datasets import VerifiedDataset
from worker.training.knowledge_expert_peft_backend import (
    FinalFfnPeftTrlTrainingBackend,
    PeftKnowledgeExpertTrainingBackend,
)
from worker.training.knowledge_expert_training import KnowledgeExpertTrainingRequest
from worker.training.process_control import CancellationToken


def _request(dataset_hash: str, **overrides):
    values = {
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "dataset_manifest": {
            "dataset_digest": "a" * 64,
            "training_dataset_identity_hash": dataset_hash,
            "tokenizer_digest": "c" * 64,
        },
        "dataset_digest": "a" * 64,
        "base_model_digest": "b" * 64,
        "tokenizer_digest": "c" * 64,
        "backend_id": "peft_trl",
        "target_layer": "final_ffn",
        "target_modules": ("gate_proj", "up_proj", "down_proj"),
        "lora_rank": 4,
        "lora_alpha": 16,
        "learning_rate": 1e-5,
        "epochs": 1.0,
        "output_relative_path": "expert.safetensors",
    }
    values.update(overrides)
    return KnowledgeExpertTrainingRequest(**values)


def _context(tmp_path, request):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    config = SimpleNamespace(
        target_modules=request.target_modules,
        lora_rank=request.lora_rank,
        lora_alpha=request.lora_alpha,
        learning_rate=request.learning_rate,
        num_train_epochs=request.epochs,
    )
    training = SimpleNamespace(
        job_id=request.task_id,
        attempt_id=request.attempt_id,
        backend=request.backend_id,
        base_model=SimpleNamespace(snapshot_hash=request.base_model_digest),
        configuration=config,
    )
    dataset = VerifiedDataset(
        train_path=tmp_path / "train.jsonl",
        validation_path=tmp_path / "validation.jsonl",
        train_records=1,
        validation_records=1,
        dataset_hash=str(request.dataset_manifest["training_dataset_identity_hash"]),
    )
    return TrainingContext(
        request=training,
        dataset=dataset,
        model_path=tmp_path / "model",
        artifact_root=artifact_root,
        checkpoint_root=tmp_path / "checkpoints",
        resume_path=None,
        cancel=CancellationToken(),
        emit=lambda event, payload: None,
    )


class Contexts:
    def __init__(self, context):
        self.context = context
        self.aborted = False

    def prepare(self, request):
        return self.context

    def abort(self, request):
        self.aborted = True


class Backend:
    name = "peft_trl"

    def availability(self):
        return True, None

    def prepare(self, context):
        return {}

    def train(self, context, prepared):
        return {}

    def evaluate(self, context, prepared, trained):
        return {"eval_loss": 0.1}

    def save(self, context, prepared, trained, metrics):
        artifact = context.artifact_root / "adapter_model.safetensors"
        artifact.write_bytes(b"real adapter bytes")
        return TrainingOutcome(metrics=metrics, artifacts=(artifact,))


def test_production_adapter_binds_existing_training_lifecycle_and_exports_safetensors(tmp_path):
    request = _request("d" * 64)
    contexts = Contexts(_context(tmp_path, request))
    adapter = PeftKnowledgeExpertTrainingBackend(
        contexts=contexts,
        backend=Backend(),
        backend_version="peft-test-1",
    )

    result = adapter.train_expert(request)

    assert result["adapter_digest"] == hashlib.sha256(b"real adapter bytes").hexdigest()
    assert result["adapter_format"] == "safetensors"
    assert result["metrics"] == {"eval_loss": 0.1}

    mismatched = _request("d" * 64, base_model_digest="e" * 64)
    with pytest.raises(ValueError, match="context_binding_mismatch"):
        adapter.train_expert(mismatched)


def test_final_ffn_backend_targets_only_last_complete_transformer_layer():
    model = SimpleNamespace(
        named_modules=lambda: iter(
            [
                (f"model.layers.{layer}.mlp.{module}", object())
                for layer in (0, 1)
                for module in ("gate_proj", "up_proj", "down_proj")
            ]
        )
    )
    config = SimpleNamespace(
        target_modules=("gate_proj", "up_proj", "down_proj"),
        lora_rank=4,
        lora_alpha=16,
        lora_dropout=0.0,
    )

    selected = FinalFfnPeftTrlTrainingBackend._create_peft_config(
        lambda **kwargs: kwargs,
        model,
        config,
    )

    assert selected["target_modules"] == [
        "model.layers.1.mlp.gate_proj",
        "model.layers.1.mlp.up_proj",
        "model.layers.1.mlp.down_proj",
    ]
