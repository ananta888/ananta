"""Optional Unsloth strategy with the same worker-facing backend contract."""

from __future__ import annotations

import importlib.util
from typing import Any

from worker.training.backends.base import TrainingBackendError, TrainingContext
from worker.training.backends.peft_trl import PeftTrlTrainingBackend


class UnslothTrainingBackend(PeftTrlTrainingBackend):
    name = "unsloth"

    def availability(self) -> tuple[bool, str | None]:
        available, detail = super().availability()
        if not available:
            return available, detail
        if importlib.util.find_spec("unsloth") is None:
            return False, "missing dependency: unsloth"
        return True, None

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "Unsloth is unavailable")
        context.emit("phase", {"phase": "loading_model"})
        try:
            from datasets import Dataset
            from unsloth import FastLanguageModel

            from worker.training.datasets import iter_jsonl

            config = context.request.configuration
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=str(context.model_path),
                max_seq_length=config.max_sequence_length,
                load_in_4bit=config.quantization == "4bit",
                local_files_only=True,
            )
            model = FastLanguageModel.get_peft_model(
                model,
                r=config.lora_rank,
                target_modules=list(config.target_modules),
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                bias="none",
                use_gradient_checkpointing="unsloth" if config.gradient_checkpointing else False,
                random_state=config.seed,
            )
            train_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.train_path)
            ]
            validation_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.validation_path)
            ]
            return {
                "model": model,
                "tokenizer": tokenizer,
                "peft_config": None,
                "train_dataset": Dataset.from_list(train_rows),
                "validation_dataset": Dataset.from_list(validation_rows),
            }
        except TrainingBackendError:
            raise
        except ImportError as exc:
            # Unsloth imports several optional accelerators lazily during
            # model construction; keep those distinct from invalid models.
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except MemoryError as exc:
            raise TrainingBackendError(
                "out_of_memory", "Unsloth model preparation exhausted memory", retryable=True
            ) from exc
        except Exception as exc:
            if "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory", "Unsloth model preparation exhausted memory", retryable=True
                ) from exc
            raise TrainingBackendError(
                "model_load_failed", f"local Unsloth model could not be prepared: {exc}"
            ) from exc

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        # SFTTrainer accepts a model that is already PEFT-wrapped; passing a
        # second peft_config would incorrectly attach another adapter.
        return super().train(context, prepared)
