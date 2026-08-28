"""Worker execution port for one Hub-bound knowledge-expert training job."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class KnowledgeExpertTrainingRequest:
    task_id: str
    attempt_id: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    dataset_manifest: Mapping[str, Any]
    dataset_digest: str
    base_model_digest: str
    tokenizer_digest: str
    backend_id: str
    target_layer: str
    target_modules: tuple[str, ...]
    lora_rank: int
    lora_alpha: int
    learning_rate: float
    epochs: float
    output_relative_path: str

    def validate(self) -> None:
        required = (
            self.task_id,
            self.attempt_id,
            self.tenant_id,
            self.workspace_id,
            self.repository_id,
            self.dataset_digest,
            self.base_model_digest,
            self.tokenizer_digest,
            self.backend_id,
        )
        if any(not str(item).strip() for item in required):
            raise ValueError("knowledge_expert_training_binding_required")
        if self.target_layer != "final_ffn":
            raise ValueError("knowledge_expert_training_target_layer_denied")
        if not self.target_modules or not all(
            module in {"gate_proj", "up_proj", "down_proj"} for module in self.target_modules
        ):
            raise ValueError("knowledge_expert_training_target_modules_denied")
        if not 1 <= self.lora_rank <= 64 or not 1 <= self.lora_alpha <= 256:
            raise ValueError("knowledge_expert_training_lora_config_invalid")
        if not 0.0 < self.learning_rate <= 0.01 or not 0.0 < self.epochs <= 100.0:
            raise ValueError("knowledge_expert_training_schedule_invalid")
        path = Path(self.output_relative_path)
        if path.is_absolute() or ".." in path.parts or path.suffix not in {"", ".safetensors"}:
            raise ValueError("knowledge_expert_training_output_path_invalid")


class KnowledgeExpertTrainingBackendPort(Protocol):
    def train_expert(self, request: KnowledgeExpertTrainingRequest) -> Mapping[str, Any]: ...

    def abort(self, request: KnowledgeExpertTrainingRequest) -> None: ...


class KnowledgeExpertTrainingExecutor:
    """Execute exactly one request; it cannot publish or activate a bank."""

    def __init__(self, backends: Mapping[str, KnowledgeExpertTrainingBackendPort]) -> None:
        self._backends = dict(backends)

    def execute(self, request: KnowledgeExpertTrainingRequest) -> dict[str, Any]:
        request.validate()
        backend = self._backends.get(request.backend_id)
        if backend is None:
            raise ValueError("knowledge_expert_training_backend_unavailable")
        try:
            raw = dict(backend.train_expert(request))
        except Exception:
            backend.abort(request)
            raise
        allowed = {"adapter_digest", "adapter_size_bytes", "adapter_format", "metrics", "backend_version"}
        digest = str(raw.get("adapter_digest") or "")
        size = raw.get("adapter_size_bytes")
        metrics = raw.get("metrics")
        if (
            set(raw) != allowed
            or raw.get("adapter_format") != "safetensors"
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or not isinstance(metrics, Mapping)
            or not str(raw.get("backend_version") or "").strip()
        ):
            backend.abort(request)
            raise ValueError("knowledge_expert_training_result_invalid")
        return {
            "schema": "ananta.knowledge-expert-training-result.v1",
            "task_id": request.task_id,
            "attempt_id": request.attempt_id,
            "dataset_digest": request.dataset_digest,
            "base_model_digest": request.base_model_digest,
            "tokenizer_digest": request.tokenizer_digest,
            **raw,
            "activation_authorized": False,
        }


__all__ = [
    "KnowledgeExpertTrainingBackendPort",
    "KnowledgeExpertTrainingExecutor",
    "KnowledgeExpertTrainingRequest",
]
