"""Deterministic compiler from the Ananta v1 job into pinned trainer configs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from worker.training.backend_config_policy import BackendConfigPolicy
from worker.training.backends.base import TrainingContext

COMPILER_VERSION = "ananta.training-backend-config-compiler.v1"
SUPPORTED_BACKEND_VERSIONS = {
    "autotrain": "0.8.36",
    "axolotl": "0.18.0",
    "llamafactory": "0.9.5",
    "torchtune": "0.6.1",
}


@dataclass(frozen=True, slots=True)
class CompiledBackendConfig:
    backend_id: str
    backend_version: str
    compiler_version: str
    sha256: str
    values: Mapping[str, Any]

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(self.values) + "\n", encoding="utf-8")
        return path


class BackendConfigCompiler:
    def compile(self, backend_id: str, context: TrainingContext) -> CompiledBackendConfig:
        builders = {
            "autotrain": self._autotrain,
            "axolotl": self._axolotl,
            "llamafactory": self._llamafactory,
            "torchtune": self._torchtune,
        }
        builder = builders.get(backend_id)
        if builder is None:
            raise ValueError("unsupported external training backend")
        values = builder(context)
        policy = BackendConfigPolicy(
            allowed_roots=(
                context.model_path,
                context.dataset.train_path.parent,
                context.artifact_root,
                context.checkpoint_root,
            )
        )
        policy.validate(values)
        return CompiledBackendConfig(
            backend_id=backend_id,
            backend_version=SUPPORTED_BACKEND_VERSIONS[backend_id],
            compiler_version=COMPILER_VERSION,
            sha256=hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest(),
            values=values,
        )

    @staticmethod
    def _common(context: TrainingContext) -> dict[str, Any]:
        config = context.request.configuration
        return {
            "model": str(context.model_path.resolve()),
            "train": str(context.dataset.train_path.resolve()),
            "validation": str(context.dataset.validation_path.resolve()),
            "output": str(context.artifact_root.resolve()),
            "checkpoints": str(context.checkpoint_root.resolve()),
            "seed": config.seed,
            "max_steps": config.max_steps,
            "epochs": config.num_train_epochs,
            "learning_rate": config.learning_rate,
            "train_batch_size": config.train_batch_size,
            "eval_batch_size": config.eval_batch_size,
            "gradient_accumulation": config.gradient_accumulation_steps,
            "eval_steps": config.eval_steps,
            "save_steps": config.save_steps,
            "sequence_length": config.max_sequence_length,
            "quantization": config.quantization,
            "lora_rank": config.lora_rank,
            "lora_alpha": config.lora_alpha,
            "lora_dropout": config.lora_dropout,
            "target_modules": list(config.target_modules),
            "resume_from_checkpoint": str(context.resume_path.resolve()) if context.resume_path is not None else None,
        }

    def _axolotl(self, context: TrainingContext) -> dict[str, Any]:
        value = self._common(context)
        result = {
            "base_model": value["model"],
            "datasets": [{"path": value["train"], "type": "completion"}],
            "test_datasets": [{"path": value["validation"], "type": "completion"}],
            "output_dir": value["output"],
            "dataset_prepared_path": str((context.checkpoint_root / "prepared").resolve()),
            "adapter": "qlora" if value["quantization"] != "none" else "lora",
            "load_in_4bit": value["quantization"] == "4bit",
            "load_in_8bit": value["quantization"] == "8bit",
            "sequence_len": value["sequence_length"],
            "micro_batch_size": value["train_batch_size"],
            "eval_batch_size": value["eval_batch_size"],
            "gradient_accumulation_steps": value["gradient_accumulation"],
            "num_epochs": value["epochs"],
            "max_steps": value["max_steps"],
            "learning_rate": value["learning_rate"],
            "eval_steps": value["eval_steps"],
            "save_steps": value["save_steps"],
            "lora_r": value["lora_rank"],
            "lora_alpha": value["lora_alpha"],
            "lora_dropout": value["lora_dropout"],
            "lora_target_modules": value["target_modules"],
            "seed": value["seed"],
            "save_safetensors": True,
        }
        if value["resume_from_checkpoint"] is not None:
            result["resume_from_checkpoint"] = value["resume_from_checkpoint"]
        return result

    def _llamafactory(self, context: TrainingContext) -> dict[str, Any]:
        value = self._common(context)
        result = {
            "model_name_or_path": value["model"],
            "dataset": value["train"],
            "eval_dataset": value["validation"],
            "output_dir": value["output"],
            "stage": "sft",
            "do_train": True,
            "do_eval": True,
            "finetuning_type": "lora",
            "quantization_bit": int(value["quantization"].removesuffix("bit"))
            if value["quantization"] != "none"
            else None,
            "cutoff_len": value["sequence_length"],
            "per_device_train_batch_size": value["train_batch_size"],
            "per_device_eval_batch_size": value["eval_batch_size"],
            "gradient_accumulation_steps": value["gradient_accumulation"],
            "num_train_epochs": value["epochs"],
            "max_steps": value["max_steps"],
            "learning_rate": value["learning_rate"],
            "eval_steps": value["eval_steps"],
            "save_steps": value["save_steps"],
            "lora_rank": value["lora_rank"],
            "lora_alpha": value["lora_alpha"],
            "lora_dropout": value["lora_dropout"],
            "lora_target": ",".join(value["target_modules"]),
            "seed": value["seed"],
            "report_to": "none",
        }
        if value["resume_from_checkpoint"] is not None:
            result["resume_from_checkpoint"] = value["resume_from_checkpoint"]
        return result

    def _autotrain(self, context: TrainingContext) -> dict[str, Any]:
        value = self._common(context)
        result = {
            "task": "llm-sft",
            "base_model": value["model"],
            "project_name": context.request.job_id,
            "project_dir": value["output"],
            "backend": "local",
            "data": {
                "path": value["train"],
                "valid_path": value["validation"],
                "chat_template": "tokenizer",
            },
            "params": {
                "block_size": value["sequence_length"],
                "epochs": value["epochs"],
                "max_steps": value["max_steps"],
                "batch_size": value["train_batch_size"],
                "gradient_accumulation": value["gradient_accumulation"],
                "lr": value["learning_rate"],
                "peft": True,
                "quantization": "int4" if value["quantization"] == "4bit" else value["quantization"],
                "target_modules": value["target_modules"],
                "seed": value["seed"],
            },
            "hub": {"push_to_hub": False},
            "log": "none",
        }
        if value["resume_from_checkpoint"] is not None:
            result["params"]["resume_from_checkpoint"] = value["resume_from_checkpoint"]
        return result

    def _torchtune(self, context: TrainingContext) -> dict[str, Any]:
        value = self._common(context)
        result = {
            "model_name_or_path": value["model"],
            "dataset": value["train"],
            "validation_path": value["validation"],
            "output_dir": value["output"],
            "checkpointer": {"checkpoint_dir": value["checkpoints"], "output_dir": value["output"]},
            "seed": value["seed"],
            "epochs": value["epochs"],
            "max_steps_per_epoch": value["max_steps"],
            "batch_size": value["train_batch_size"],
            "gradient_accumulation_steps": value["gradient_accumulation"],
            "max_seq_len": value["sequence_length"],
            "optimizer": {"lr": value["learning_rate"]},
            "lora_rank": value["lora_rank"],
            "lora_alpha": value["lora_alpha"],
            "lora_dropout": value["lora_dropout"],
            "lora_attn_modules": value["target_modules"],
        }
        if value["resume_from_checkpoint"] is not None:
            result["resume_from_checkpoint"] = True
            result["checkpointer"]["checkpoint_dir"] = value["resume_from_checkpoint"]
        return result


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "BackendConfigCompiler",
    "COMPILER_VERSION",
    "CompiledBackendConfig",
    "SUPPORTED_BACKEND_VERSIONS",
    "canonical_json",
]
