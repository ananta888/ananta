"""Production PEFT adapter for one Hub-bound final-FFN knowledge expert."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Protocol

from worker.training.backends.base import TrainingBackend, TrainingContext, run_backend
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.knowledge_expert_training import KnowledgeExpertTrainingRequest

_LAYER = re.compile(r"(?:^|\.)(?:layers|h)\.(\d+)\.")


class KnowledgeExpertTrainingContextPort(Protocol):
    def prepare(self, request: KnowledgeExpertTrainingRequest) -> TrainingContext: ...

    def abort(self, request: KnowledgeExpertTrainingRequest) -> None: ...


class FinalFfnPeftTrlTrainingBackend(PeftTrlTrainingBackend):
    """Limit PEFT target modules to the actual final transformer block."""

    @staticmethod
    def _create_peft_config(lora_config_type: Any, model: Any, config: Any) -> Any:
        requested = set(config.target_modules)
        targets: dict[int, set[str]] = {}
        exact_names: dict[tuple[int, str], str] = {}
        for name, _module in model.named_modules():
            match = _LAYER.search(str(name))
            leaf = str(name).rsplit(".", 1)[-1]
            if match is None or leaf not in requested:
                continue
            layer = int(match.group(1))
            targets.setdefault(layer, set()).add(leaf)
            exact_names[(layer, leaf)] = str(name)
        eligible = sorted(layer for layer, leaves in targets.items() if leaves == requested)
        if not eligible:
            raise ValueError("knowledge_expert_final_ffn_modules_unavailable")
        final_layer = eligible[-1]
        selected = [exact_names[(final_layer, module)] for module in config.target_modules]
        return lora_config_type(
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=selected,
        )


class PeftKnowledgeExpertTrainingBackend:
    """Bridge the expert port to Ananta's admitted local PEFT lifecycle."""

    def __init__(
        self,
        *,
        contexts: KnowledgeExpertTrainingContextPort,
        backend_version: str,
        backend: TrainingBackend | None = None,
    ) -> None:
        self._contexts = contexts
        self._backend = backend or FinalFfnPeftTrlTrainingBackend()
        self._backend_version = str(backend_version or "").strip()
        if not self._backend_version:
            raise ValueError("knowledge_expert_training_backend_version_required")

    def train_expert(self, request: KnowledgeExpertTrainingRequest) -> dict[str, Any]:
        context = self._contexts.prepare(request)
        self._validate_binding(request, context)
        outcome = run_backend(self._backend, context)
        candidates = [path for path in outcome.artifacts if path.name == "adapter_model.safetensors"]
        if len(candidates) != 1:
            raise ValueError("knowledge_expert_training_adapter_artifact_invalid")
        artifact = candidates[0]
        root = context.artifact_root.resolve()
        resolved = artifact.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("knowledge_expert_training_adapter_artifact_invalid") from exc
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size < 1:
            raise ValueError("knowledge_expert_training_adapter_artifact_invalid")
        return {
            "adapter_digest": _file_sha256(artifact),
            "adapter_size_bytes": artifact.stat().st_size,
            "adapter_format": "safetensors",
            "metrics": dict(outcome.metrics),
            "backend_version": self._backend_version,
        }

    def abort(self, request: KnowledgeExpertTrainingRequest) -> None:
        self._contexts.abort(request)

    @staticmethod
    def _validate_binding(request: KnowledgeExpertTrainingRequest, context: TrainingContext) -> None:
        training = context.request
        dataset_manifest = request.dataset_manifest
        configuration = training.configuration
        if (
            training.job_id != request.task_id
            or training.attempt_id != request.attempt_id
            or training.backend != request.backend_id
            or training.base_model.snapshot_hash != request.base_model_digest
            or context.dataset.dataset_hash != dataset_manifest.get("training_dataset_identity_hash")
            or request.dataset_digest != dataset_manifest.get("dataset_digest")
            or request.tokenizer_digest != dataset_manifest.get("tokenizer_digest")
            or tuple(configuration.target_modules) != request.target_modules
            or configuration.lora_rank != request.lora_rank
            or configuration.lora_alpha != request.lora_alpha
            or not math.isclose(configuration.learning_rate, request.learning_rate)
            or not math.isclose(configuration.num_train_epochs, request.epochs)
        ):
            raise ValueError("knowledge_expert_training_context_binding_mismatch")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "FinalFfnPeftTrlTrainingBackend",
    "KnowledgeExpertTrainingContextPort",
    "PeftKnowledgeExpertTrainingBackend",
]
