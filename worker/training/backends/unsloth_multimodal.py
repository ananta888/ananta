"""Shared engine used by separate vision and audio Unsloth strategies."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.trl_compat import sequence_length_options
from worker.training.backends.unsloth_checkpoint import UnslothCheckpointLifecycle
from worker.training.datasets import iter_jsonl


class UnslothMultimodalEngine:
    """Composition helper; public backend identities remain separate."""

    _requirements = ("torch", "transformers", "datasets", "peft", "trl", "safetensors", "unsloth")

    def __init__(
        self,
        *,
        backend_name: str,
        model_class_name: str,
        media_type: str,
    ) -> None:
        self._model_class_name = model_class_name
        self._media_type = media_type
        self._lifecycle = PeftTrlTrainingBackend()
        self.checkpoint_lifecycle = UnslothCheckpointLifecycle(
            backend_name=backend_name
        )

    def availability(self) -> tuple[bool, str | None]:
        missing = [name for name in self._requirements if importlib.util.find_spec(name) is None]
        return (False, "missing dependencies: " + ", ".join(missing)) if missing else (True, None)

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "Unsloth is unavailable")
        event_modality = "vision" if self._media_type == "image" else self._media_type
        context.emit("phase", {"phase": "loading_model", "modality": event_modality})
        try:
            import unsloth
            from datasets import Dataset

            model_class = getattr(unsloth, self._model_class_name, None)
            if model_class is None:
                raise TrainingBackendError(
                    "modality_dependency_unavailable",
                    f"installed Unsloth has no {self._model_class_name}",
                )
            config = context.request.configuration
            model, processor = model_class.from_pretrained(
                model_name=str(context.model_path),
                max_seq_length=config.max_sequence_length,
                load_in_4bit=config.quantization == "4bit",
                local_files_only=True,
                trust_remote_code=False,
                use_gradient_checkpointing="unsloth" if config.gradient_checkpointing else False,
            )
            peft_kwargs: dict[str, Any] = {
                "r": config.lora_rank,
                "target_modules": list(config.target_modules),
                "lora_alpha": config.lora_alpha,
                "lora_dropout": config.lora_dropout,
                "bias": "none",
                "use_gradient_checkpointing": "unsloth" if config.gradient_checkpointing else False,
                "random_state": config.seed,
            }
            if self._media_type == "image":
                peft_kwargs.update(
                    {
                        "finetune_vision_layers": True,
                        "finetune_language_layers": True,
                        "finetune_attention_modules": True,
                        "finetune_mlp_modules": True,
                    }
                )
            model = model_class.get_peft_model(model, **peft_kwargs)
            for_training = getattr(model_class, "for_training", None)
            if callable(for_training):
                for_training(model)
            train_rows = _materialize_rows(
                iter_jsonl(context.dataset.train_path),
                root=context.dataset.train_path.parent,
                media_type=self._media_type,
            )
            validation_rows = _materialize_rows(
                iter_jsonl(context.dataset.validation_path),
                root=context.dataset.validation_path.parent,
                media_type=self._media_type,
            )
            return {
                "model": model,
                "tokenizer": processor,
                "processor": processor,
                "peft_config": None,
                "train_dataset": Dataset.from_list(train_rows),
                "validation_dataset": Dataset.from_list(validation_rows),
            }
        except TrainingBackendError:
            raise
        except ImportError as exc:
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except MemoryError as exc:
            raise TrainingBackendError(
                "out_of_memory",
                "multimodal model preparation exhausted memory",
                retryable=True,
            ) from exc
        except Exception as exc:
            if "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory",
                    "multimodal model preparation exhausted memory",
                    retryable=True,
                ) from exc
            raise TrainingBackendError(
                "model_load_failed",
                f"local {self._media_type} model could not be prepared: {exc}",
            ) from exc

    def train(self, context: TrainingContext, prepared: Mapping[str, Any]) -> dict[str, Any]:
        try:
            from trl import SFTConfig, SFTTrainer
            from unsloth.trainer import UnslothVisionDataCollator
        except ImportError as exc:
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        config = context.request.configuration
        callback = self._lifecycle._trainer_callback(context, _trainer_callback_base())
        try:
            args = SFTConfig(
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
                remove_unused_columns=False,
                dataset_text_field="",
                dataset_kwargs={"skip_prepare_dataset": True},
                report_to=[],
                **sequence_length_options(SFTConfig, config.max_sequence_length),
            )
            trainer = SFTTrainer(
                model=prepared["model"],
                args=args,
                train_dataset=prepared["train_dataset"],
                eval_dataset=prepared["validation_dataset"],
                processing_class=prepared["processor"],
                data_collator=UnslothVisionDataCollator(prepared["model"], prepared["processor"]),
                callbacks=[callback],
            )
            result = trainer.train(resume_from_checkpoint=str(context.resume_path) if context.resume_path else None)
            return {"trainer": trainer, "train_metrics": dict(result.metrics)}
        except TrainingBackendError:
            raise
        except Exception as exc:
            if context.cancel.cancelled:
                context.cancel.raise_if_cancelled()
            if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory",
                    "multimodal training exhausted memory",
                    retryable=True,
                ) from exc
            raise TrainingBackendError("training_failed", f"multimodal training failed: {exc}") from exc

    def evaluate(
        self,
        context: TrainingContext,
        prepared: Mapping[str, Any],
        trained: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._lifecycle.evaluate(context, prepared, trained)

    def save(
        self,
        context: TrainingContext,
        prepared: Mapping[str, Any],
        trained: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        return self._lifecycle.save(context, prepared, trained, metrics)


def _trainer_callback_base() -> type[Any]:
    try:
        from transformers import TrainerCallback
    except ImportError as exc:
        raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
    return TrainerCallback


def _materialize_rows(rows: Any, *, root: Path, media_type: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    media_count = 0
    admitted_root = root.resolve(strict=True)
    for raw in rows:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("messages"), list):
            raise TrainingBackendError(
                "invalid_modality_dataset",
                f"{media_type} datasets require a messages array",
            )
        row = dict(raw)
        messages: list[dict[str, Any]] = []
        for raw_message in raw["messages"]:
            if not isinstance(raw_message, Mapping):
                raise TrainingBackendError("invalid_modality_dataset", "message must be an object")
            message = dict(raw_message)
            content = message.get("content")
            if isinstance(content, list):
                normalized_content: list[Any] = []
                for raw_part in content:
                    part = dict(raw_part) if isinstance(raw_part, Mapping) else raw_part
                    if isinstance(part, dict) and part.get("type") == media_type:
                        reference = part.get(media_type)
                        if not isinstance(reference, str) or not reference:
                            raise TrainingBackendError(
                                "invalid_modality_dataset",
                                f"{media_type} content requires a relative file reference",
                            )
                        candidate = (admitted_root / reference).resolve(strict=True)
                        if (
                            candidate.is_symlink()
                            or not candidate.is_file()
                            or admitted_root not in candidate.parents
                        ):
                            raise TrainingBackendError(
                                "modality_path_escape",
                                f"{media_type} content escapes its dataset root",
                            )
                        part[media_type] = str(candidate)
                        media_count += 1
                    normalized_content.append(part)
                message["content"] = normalized_content
            messages.append(message)
        row["messages"] = messages
        result.append(row)
    if not result or media_count == 0:
        raise TrainingBackendError(
            "invalid_modality_dataset",
            f"{media_type} dataset contains no admitted media references",
        )
    return result
