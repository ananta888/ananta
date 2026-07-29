"""Independent Unsloth sentence-embedding training strategy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.unsloth_checkpoint import UnslothCheckpointLifecycle
from worker.training.datasets import iter_jsonl


class UnslothEmbeddingTrainingBackend:
    name = "unsloth_embedding"
    _requirements = (
        "torch",
        "datasets",
        "peft",
        "safetensors",
        "sentence_transformers",
        "unsloth",
    )

    def __init__(self) -> None:
        self._lifecycle = PeftTrlTrainingBackend()
        self.checkpoint_lifecycle = UnslothCheckpointLifecycle(
            backend_name=self.name
        )

    def availability(self) -> tuple[bool, str | None]:
        missing = [name for name in self._requirements if importlib.util.find_spec(name) is None]
        return (False, "missing dependencies: " + ", ".join(missing)) if missing else (True, None)

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "embedding dependencies are unavailable")
        context.emit("phase", {"phase": "loading_model", "modality": "embedding"})
        try:
            import torch
            from datasets import Dataset
            from sentence_transformers.losses import CoSENTLoss
            from unsloth import FastSentenceTransformer

            config = context.request.configuration
            model = FastSentenceTransformer.from_pretrained(
                str(context.model_path),
                load_in_4bit=config.quantization == "4bit",
                device_map="cuda" if torch.cuda.is_available() else "cpu",
                local_files_only=True,
                trust_remote_code=False,
            )
            model = FastSentenceTransformer.get_peft_model(
                model,
                r=config.lora_rank,
                target_modules=list(config.target_modules),
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                bias="none",
            )
            train_rows = _embedding_rows(iter_jsonl(context.dataset.train_path))
            validation_rows = _embedding_rows(iter_jsonl(context.dataset.validation_path))
            return {
                "model": model,
                "train_dataset": Dataset.from_list(train_rows),
                "validation_dataset": Dataset.from_list(validation_rows),
                "loss": CoSENTLoss(model),
            }
        except TrainingBackendError:
            raise
        except ImportError as exc:
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except Exception as exc:
            if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
                raise TrainingBackendError("out_of_memory", "embedding model preparation exhausted memory", retryable=True) from exc
            raise TrainingBackendError("model_load_failed", f"embedding model could not be prepared: {exc}") from exc

    def train(self, context: TrainingContext, prepared: Mapping[str, Any]) -> dict[str, Any]:
        try:
            import torch
            from sentence_transformers import SentenceTransformerTrainer, SentenceTransformerTrainingArguments
        except ImportError as exc:
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        config = context.request.configuration
        callback = self._lifecycle._trainer_callback(
            context,
            _trainer_callback_base(),
        )
        try:
            arguments = SentenceTransformerTrainingArguments(
                output_dir=str(context.checkpoint_root),
                max_steps=config.max_steps,
                num_train_epochs=config.num_train_epochs,
                per_device_train_batch_size=config.train_batch_size,
                per_device_eval_batch_size=config.eval_batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                learning_rate=config.learning_rate,
                eval_strategy="steps",
                eval_steps=config.eval_steps,
                save_strategy="steps",
                save_steps=config.save_steps,
                logging_steps=1,
                seed=config.seed,
                data_seed=config.seed,
                fp16=bool(torch.cuda.is_available() and not torch.cuda.is_bf16_supported()),
                bf16=bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported()),
                report_to=[],
            )
            trainer = SentenceTransformerTrainer(
                model=prepared["model"],
                args=arguments,
                train_dataset=prepared["train_dataset"],
                eval_dataset=prepared["validation_dataset"],
                loss=prepared["loss"],
                callbacks=[callback],
            )
            result = trainer.train(resume_from_checkpoint=str(context.resume_path) if context.resume_path else None)
            return {"trainer": trainer, "train_metrics": dict(result.metrics)}
        except Exception as exc:
            if context.cancel.cancelled:
                context.cancel.raise_if_cancelled()
            if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
                raise TrainingBackendError("out_of_memory", "embedding training exhausted memory", retryable=True) from exc
            raise TrainingBackendError("training_failed", f"embedding training failed: {exc}") from exc

    def evaluate(self, context: TrainingContext, prepared: Any, trained: Any) -> Mapping[str, Any]:
        context.emit("phase", {"phase": "evaluating", "modality": "embedding"})
        try:
            metrics = dict(trained["trainer"].evaluate(metric_key_prefix="embedding"))
        except Exception as exc:
            raise TrainingBackendError("evaluation_failed", f"embedding evaluation failed: {exc}") from exc
        return {
            "validation_records": context.dataset.validation_records,
            "embedding": {
                key: float(value)
                for key, value in metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
            "train": dict(trained["train_metrics"]),
        }

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        context.emit("phase", {"phase": "saving", "modality": "embedding"})
        try:
            context.artifact_root.mkdir(parents=True, exist_ok=True)
            trained["trainer"].model.save_pretrained(str(context.artifact_root), safe_serialization=True)
        except Exception as exc:
            raise TrainingBackendError("artifact_save_failed", f"embedding artifacts could not be saved: {exc}") from exc
        artifacts = tuple(
            path
            for path in context.artifact_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        best = getattr(trained["trainer"].state, "best_model_checkpoint", None)
        return TrainingOutcome(metrics=metrics, artifacts=artifacts, best_checkpoint=Path(best) if best else None)


def _embedding_rows(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TrainingBackendError("invalid_embedding_dataset", "embedding record must be an object")
        left = raw.get("sentence_A")
        right = raw.get("sentence_B")
        label = raw.get("label")
        if (
            not isinstance(left, str)
            or not left.strip()
            or not isinstance(right, str)
            or not right.strip()
            or isinstance(label, bool)
            or not isinstance(label, (int, float))
            or not 0.0 <= float(label) <= 1.0
        ):
            raise TrainingBackendError(
                "invalid_embedding_dataset",
                "embedding records require sentence_A, sentence_B and label in [0,1]",
            )
        result.append({"sentence_A": left, "sentence_B": right, "label": float(label)})
    if not result:
        raise TrainingBackendError("invalid_embedding_dataset", "embedding dataset must not be empty")
    return result


def _trainer_callback_base() -> type[Any]:
    try:
        from transformers import TrainerCallback
    except ImportError as exc:
        raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
    return TrainerCallback
