from __future__ import annotations

import pytest

from worker.training.knowledge_expert_training import (
    KnowledgeExpertTrainingExecutor,
    KnowledgeExpertTrainingRequest,
)


def _request(**overrides):
    values = {
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "dataset_manifest": {},
        "dataset_digest": "a" * 64,
        "base_model_digest": "b" * 64,
        "tokenizer_digest": "c" * 64,
        "backend_id": "fake",
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


class _Backend:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "adapter_digest": "d" * 64,
            "adapter_size_bytes": 1024,
            "adapter_format": "safetensors",
            "metrics": {"loss": 0.1},
            "backend_version": "1",
        }
        self.error = error
        self.aborted = False

    def train_expert(self, request):
        if self.error:
            raise self.error
        return self.result

    def abort(self, request):
        self.aborted = True


def test_training_is_replaceable_final_ffn_only_and_never_activates():
    backend = _Backend()
    result = KnowledgeExpertTrainingExecutor({"fake": backend}).execute(_request())
    assert result["activation_authorized"] is False
    with pytest.raises(ValueError, match="target_layer_denied"):
        KnowledgeExpertTrainingExecutor({"fake": backend}).execute(_request(target_layer="all_layers"))


def test_training_failure_and_invalid_artifact_abort_backend_state():
    failed = _Backend(error=RuntimeError("training failed"))
    with pytest.raises(RuntimeError, match="training failed"):
        KnowledgeExpertTrainingExecutor({"fake": failed}).execute(_request())
    assert failed.aborted is True
    invalid = _Backend(result={"adapter_digest": "bad"})
    with pytest.raises(ValueError, match="result_invalid"):
        KnowledgeExpertTrainingExecutor({"fake": invalid}).execute(_request())
    assert invalid.aborted is True
