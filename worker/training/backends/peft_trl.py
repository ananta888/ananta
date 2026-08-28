"""Local-only PEFT/TRL LoRA and QLoRA strategy.

All heavyweight imports occur inside methods so the worker control API stays
available even when an optional engine image is not installed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.datasets import iter_jsonl
from worker.training.local_transformers_tokenizer import load_local_tokenizer


class PeftTrlTrainingBackend:
    name = "peft_trl"
    _requirements = ("torch", "transformers", "datasets", "peft", "trl", "safetensors")

    def availability(self) -> tuple[bool, str | None]:
        missing = [name for name in self._requirements if importlib.util.find_spec(name) is None]
        if missing:
            return False, "missing dependencies: " + ", ".join(missing)
        return True, None

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "PEFT/TRL dependencies are unavailable")
        context.emit("phase", {"phase": "loading_model"})
        try:
            import torch
            from datasets import Dataset
            from peft import LoraConfig, prepare_model_for_kbit_training
            from transformers import AutoModelForCausalLM, BitsAndBytesConfig
        except ImportError as exc:  # pragma: no cover - guarded by availability, version-dependent
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc

        config = context.request.configuration
        quantization_config = None
        if config.quantization in {"4bit", "8bit"}:
            if not torch.cuda.is_available():
                raise TrainingBackendError(
                    "resource_unavailable",
                    f"{config.quantization} QLoRA requires an admitted CUDA device",
                )
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=config.quantization == "4bit",
                load_in_8bit=config.quantization == "8bit",
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        try:
            tokenizer = load_local_tokenizer(context.model_path)
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token = tokenizer.eos_token
            model = AutoModelForCausalLM.from_pretrained(
                str(context.model_path),
                local_files_only=True,
                trust_remote_code=False,
                quantization_config=quantization_config,
                torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
                device_map="auto" if torch.cuda.is_available() else None,
            )
            if quantization_config is not None:
                model = prepare_model_for_kbit_training(
                    model,
                    use_gradient_checkpointing=config.gradient_checkpointing,
                )
            elif config.gradient_checkpointing:
                model.gradient_checkpointing_enable()
            peft_config = self._create_peft_config(LoraConfig, model, config)
            train_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.train_path)
            ]
            validation_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.validation_path)
            ]
            return {
                "model": model,
                "tokenizer": tokenizer,
                "peft_config": peft_config,
                "train_dataset": Dataset.from_list(train_rows),
                "validation_dataset": Dataset.from_list(validation_rows),
            }
        except TrainingBackendError:
            raise
        except ImportError as exc:
            # Optional runtime dependencies (for example accelerate or
            # bitsandbytes) can be imported lazily by ``from_pretrained`` even
            # when the top-level packages passed the availability probe.
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except MemoryError as exc:
            raise TrainingBackendError("out_of_memory", "model preparation exhausted memory", retryable=True) from exc
        except Exception as exc:
            if "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory", "model preparation exhausted memory", retryable=True
                ) from exc
            raise TrainingBackendError("model_load_failed", f"local model could not be prepared: {exc}") from exc

    @staticmethod
    def _create_peft_config(lora_config_type: Any, model: Any, config: Any) -> Any:
        del model
        return lora_config_type(
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=list(config.target_modules),
        )

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        try:
            from transformers import EarlyStoppingCallback, TrainerCallback
            from trl import SFTConfig, SFTTrainer
        except ImportError as exc:  # pragma: no cover - optional image
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc

        callback = self._trainer_callback(context, TrainerCallback)
        config = context.request.configuration
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
                load_best_model_at_end=True,
                metric_for_best_model="eval_loss",
                greater_is_better=False,
                seed=config.seed,
                data_seed=config.seed,
                max_seq_length=config.max_sequence_length,
                report_to=[],
            )
            callbacks: list[Any] = [callback]
            if config.early_stopping_patience:
                callbacks.append(EarlyStoppingCallback(early_stopping_patience=config.early_stopping_patience))
            trainer = SFTTrainer(
                model=prepared["model"],
                args=args,
                train_dataset=prepared["train_dataset"],
                eval_dataset=prepared["validation_dataset"],
                processing_class=prepared["tokenizer"],
                peft_config=prepared["peft_config"],
                callbacks=callbacks,
            )
            result = trainer.train(resume_from_checkpoint=str(context.resume_path) if context.resume_path else None)
        except ImportError as exc:
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except Exception as exc:
            if context.cancel.cancelled:
                context.cancel.raise_if_cancelled()
            if isinstance(exc, (MemoryError,)) or "out of memory" in str(exc).lower():
                raise TrainingBackendError("out_of_memory", "training exhausted memory", retryable=True) from exc
            raise TrainingBackendError("training_failed", f"PEFT/TRL training failed: {exc}") from exc
        return {"trainer": trainer, "train_metrics": dict(result.metrics)}

    def evaluate(self, context: TrainingContext, prepared: Any, trained: Any) -> Mapping[str, Any]:
        context.emit("phase", {"phase": "evaluating"})
        trainer = trained["trainer"]
        model = trainer.model
        try:
            adapter_metrics = trainer.evaluate(metric_key_prefix="adapter")
            disable_adapter = getattr(model, "disable_adapter", None)
            if callable(disable_adapter):
                with disable_adapter():
                    base_metrics = trainer.evaluate(metric_key_prefix="base")
            else:
                base_metrics = {}
        except Exception as exc:
            if "out of memory" in str(exc).lower():
                raise TrainingBackendError("out_of_memory", "evaluation exhausted memory", retryable=True) from exc
            raise TrainingBackendError("evaluation_failed", f"validation evaluation failed: {exc}") from exc
        base_loss = self._finite(base_metrics.get("base_loss"))
        adapter_loss = self._finite(adapter_metrics.get("adapter_loss"))
        delta = adapter_loss - base_loss if base_loss is not None and adapter_loss is not None else None
        return {
            "validation_records": context.dataset.validation_records,
            "base": {"eval_loss": base_loss},
            "adapter": {"eval_loss": adapter_loss},
            "delta": {"eval_loss": delta},
            "train": {key: self._finite(value) for key, value in trained["train_metrics"].items()},
        }

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        context.emit("phase", {"phase": "saving"})
        context.artifact_root.mkdir(parents=True, exist_ok=True)
        try:
            trained["trainer"].model.save_pretrained(str(context.artifact_root), safe_serialization=True)
            prepared["tokenizer"].save_pretrained(str(context.artifact_root))
            metrics_path = context.artifact_root / "evaluation.json"
            metrics_path.write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            raise TrainingBackendError("artifact_save_failed", f"adapter artifacts could not be saved: {exc}") from exc
        allowed = {
            "adapter_config.json",
            "adapter_model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "evaluation.json",
        }
        artifacts = tuple(path for path in context.artifact_root.iterdir() if path.is_file() and path.name in allowed)
        best = getattr(trained["trainer"].state, "best_model_checkpoint", None)
        return TrainingOutcome(metrics=metrics, artifacts=artifacts, best_checkpoint=Path(best) if best else None)

    @staticmethod
    def _render_record(record: Mapping[str, Any], tokenizer: Any) -> str:
        if isinstance(record.get("text"), str):
            return str(record["text"])
        if isinstance(record.get("messages"), list):
            apply_template = getattr(tokenizer, "apply_chat_template", None)
            if callable(apply_template):
                return str(apply_template(record["messages"], tokenize=False, add_generation_prompt=False))
            return "\n".join(f"{item['role']}: {item['content']}" for item in record["messages"])
        instruction = str(record.get("instruction") or "")
        response = str(record.get("output") or "")
        input_text = str(record.get("input") or "")
        return f"Instruction:\n{instruction}\nInput:\n{input_text}\nResponse:\n{response}"

    @staticmethod
    def _finite(value: Any) -> float | int | None:
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            return None
        result = float(value)
        return result if math.isfinite(result) else None

    @staticmethod
    def _trainer_callback(context: TrainingContext, base: type[Any]) -> Any:
        sample = {"time": time.monotonic(), "tokens": 0}

        class WorkerCallback(base):
            def on_log(self, args: Any, state: Any, control: Any, logs: Any = None, **kwargs: Any) -> Any:
                context.cancel.raise_if_cancelled()
                values = logs or {}
                now = time.monotonic()
                observed_tokens = getattr(state, "num_input_tokens_seen", 0)
                payload = {
                    "step": int(state.global_step),
                    "max_steps": int(state.max_steps),
                    "epoch": float(state.epoch or 0.0),
                    "loss": values.get("loss"),
                    "eval_loss": values.get("eval_loss"),
                    "learning_rate": values.get("learning_rate"),
                }
                if (
                    isinstance(observed_tokens, int)
                    and observed_tokens >= sample["tokens"]
                    and now > sample["time"]
                ):
                    payload["tokens_per_second"] = (observed_tokens - sample["tokens"]) / (now - sample["time"])
                    sample["tokens"] = observed_tokens
                    sample["time"] = now
                try:
                    import torch

                    cuda = getattr(torch, "cuda", None)
                    if cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available():
                        payload["vram_used_bytes"] = int(
                            cuda.memory_allocated()
                        )
                        utilization = getattr(cuda, "utilization", None)
                        if callable(utilization):
                            payload["gpu_utilization_percent"] = float(utilization())
                except (ImportError, RuntimeError, TypeError, ValueError):
                    pass
                context.emit("progress", {key: value for key, value in payload.items() if value is not None})
                return control

            def on_save(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
                context.emit("checkpoint", {"step": int(state.global_step), "name": f"checkpoint-{state.global_step}"})
                return control

        return WorkerCallback()
