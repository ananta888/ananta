"""Base-vs-adapter evaluation port and local backend strategies."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from ananta_contracts.lora_evaluation import score_evaluation_output
from worker.training.backends.base import ProgressCallback, TrainingBackendError
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.contracts import AdapterEvaluationJobRequest
from worker.training.datasets import VerifiedValidationDataset, iter_jsonl
from worker.training.local_transformers_tokenizer import load_local_tokenizer
from worker.training.process_control import CancellationToken


@dataclass(frozen=True)
class AdapterEvaluationContext:
    request: AdapterEvaluationJobRequest
    dataset: VerifiedValidationDataset
    model_path: Path
    adapter_path: Path
    artifact_root: Path
    cancel: CancellationToken
    emit: ProgressCallback


@dataclass(frozen=True)
class AdapterEvaluationOutcome:
    metrics: Mapping[str, Any]
    artifacts: tuple[Path, ...]


class AdapterEvaluator(Protocol):
    name: str

    def evaluate_existing_adapter(self, context: AdapterEvaluationContext) -> AdapterEvaluationOutcome: ...


class AdapterModelLoader(Protocol):
    requirements: tuple[str, ...]

    def load(self, context: AdapterEvaluationContext, torch_module: Any) -> tuple[Any, Any]: ...


class PeftAdapterModelLoader:
    requirements = ("peft",)

    def load(self, context: AdapterEvaluationContext, torch_module: Any) -> tuple[Any, Any]:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, BitsAndBytesConfig

        configuration = context.request.configuration
        tokenizer = load_local_tokenizer(context.model_path)
        quantization = None
        if configuration.quantization in {"4bit", "8bit"}:
            if not torch_module.cuda.is_available():
                raise TrainingBackendError(
                    "resource_unavailable",
                    f"{configuration.quantization} adapter evaluation requires CUDA",
                )
            quantization = BitsAndBytesConfig(
                load_in_4bit=configuration.quantization == "4bit",
                load_in_8bit=configuration.quantization == "8bit",
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch_module.bfloat16,
            )
        base_model = AutoModelForCausalLM.from_pretrained(
            str(context.model_path),
            local_files_only=True,
            trust_remote_code=False,
            quantization_config=quantization,
            torch_dtype=torch_module.bfloat16 if torch_module.cuda.is_available() else torch_module.float32,
            device_map="auto" if torch_module.cuda.is_available() else None,
        )
        return (
            PeftModel.from_pretrained(base_model, str(context.adapter_path), is_trainable=False),
            tokenizer,
        )


class UnslothAdapterModelLoader:
    requirements = ("peft", "unsloth")

    def __init__(self, fast_language_model: Any | None = None) -> None:
        self._fast_language_model = fast_language_model

    def load(self, context: AdapterEvaluationContext, torch_module: Any) -> tuple[Any, Any]:
        fast_language_model = self._fast_language_model
        if fast_language_model is None:
            from unsloth import FastLanguageModel

            fast_language_model = FastLanguageModel
        configuration = context.request.configuration
        if configuration.quantization in {"4bit", "8bit"} and not torch_module.cuda.is_available():
            raise TrainingBackendError(
                "resource_unavailable",
                f"{configuration.quantization} adapter evaluation requires CUDA",
            )
        model, tokenizer = fast_language_model.from_pretrained(
            model_name=str(context.adapter_path),
            max_seq_length=configuration.max_sequence_length,
            load_in_4bit=configuration.quantization == "4bit",
            load_in_8bit=configuration.quantization == "8bit",
            load_in_16bit=configuration.quantization == "none",
            local_files_only=True,
            trust_remote_code=False,
        )
        fast_language_model.for_inference(model)
        return model, tokenizer


class MockAdapterEvaluator:
    name = "mock"

    def evaluate_existing_adapter(self, context: AdapterEvaluationContext) -> AdapterEvaluationOutcome:
        context.cancel.raise_if_cancelled()
        context.emit("phase", {"phase": "evaluating_base"})
        base_loss = 1.0
        context.cancel.raise_if_cancelled()
        context.emit("phase", {"phase": "evaluating_adapter"})
        adapter_loss = 0.75
        sample_count = min(context.request.configuration.max_samples, 20)
        samples = []
        for index, row in enumerate(iter_jsonl(context.dataset.validation_path)):
            if index >= sample_count:
                break
            reference = _prompt_reference(row)
            expected = _expected_output(row)[:16_000]
            base_output = f"[mock-base:{reference[:12]}]"
            adapter_output = expected or f"[mock-adapter:{reference[:12]}]"
            base_score = score_evaluation_output(
                context.request.configuration.scorer_name,
                base_output,
                expected_output=expected,
            )
            adapter_score = score_evaluation_output(
                context.request.configuration.scorer_name,
                adapter_output,
                expected_output=expected,
            )
            samples.append(
                {
                    "id": reference,
                    "record_index": index,
                    "base_output": base_output,
                    "adapter_output": adapter_output,
                    "expected_output": expected or None,
                    "base_score": base_score,
                    "adapter_score": adapter_score,
                    "winner": _score_winner(base_score, adapter_score),
                }
            )
        metrics = _comparison_metrics(
            base_loss,
            adapter_loss,
            context.dataset.validation_records,
            samples=samples,
            scorer_name=context.request.configuration.scorer_name,
        )
        return _write_evaluation(context, metrics)


class PeftAdapterEvaluator:
    name = "peft_trl"
    _requirements = ("torch", "transformers", "datasets")

    def __init__(self, model_loader: AdapterModelLoader | None = None) -> None:
        self._model_loader = model_loader or PeftAdapterModelLoader()

    def evaluate_existing_adapter(self, context: AdapterEvaluationContext) -> AdapterEvaluationOutcome:
        missing = [
            name
            for name in (*self._requirements, *self._model_loader.requirements)
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            raise TrainingBackendError("dependency_unavailable", "missing dependencies: " + ", ".join(missing))
        try:
            import torch
            from datasets import Dataset
            from transformers import (
                DataCollatorForLanguageModeling,
                Trainer,
                TrainingArguments,
            )
        except ImportError as exc:  # pragma: no cover - optional worker image
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc

        configuration = context.request.configuration
        rows = list(iter_jsonl(context.dataset.validation_path))
        if len(rows) > configuration.max_samples:
            indices = sorted(random.Random(configuration.seed).sample(range(len(rows)), configuration.max_samples))
            rows = [rows[index] for index in indices]
        context.emit("phase", {"phase": "loading_model"})
        try:
            adapter_model, tokenizer = self._model_loader.load(context, torch)
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token = tokenizer.eos_token
            texts = [PeftTrlTrainingBackend._render_record(row, tokenizer) for row in rows]
            tokenized = Dataset.from_dict({"text": texts}).map(
                lambda batch: tokenizer(
                    batch["text"],
                    truncation=True,
                    max_length=configuration.max_sequence_length,
                ),
                batched=True,
                remove_columns=["text"],
            )
            arguments = TrainingArguments(
                output_dir=str(context.artifact_root.parent / "evaluation-scratch"),
                per_device_eval_batch_size=configuration.batch_size,
                seed=configuration.seed,
                report_to=[],
            )
            collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
            context.cancel.raise_if_cancelled()
            context.emit("phase", {"phase": "evaluating_base"})
            base_metrics, adapter_metrics = _evaluate_base_and_adapter(
                adapter_model=adapter_model,
                trainer_factory=Trainer,
                trainer_arguments=arguments,
                eval_dataset=tokenized,
                data_collator=collator,
                before_adapter_evaluation=context.cancel.raise_if_cancelled,
                emit=context.emit,
            )
            context.cancel.raise_if_cancelled()
            samples = _generate_comparisons(
                context=context,
                rows=rows,
                tokenizer=tokenizer,
                adapter_model=adapter_model,
                torch_module=torch,
            )
        except TrainingBackendError:
            raise
        except Exception as exc:
            if context.cancel.cancelled:
                context.cancel.raise_if_cancelled()
            if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory", "adapter evaluation exhausted memory", retryable=True
                ) from exc
            raise TrainingBackendError("evaluation_failed", f"base-vs-adapter evaluation failed: {exc}") from exc
        base_loss = _finite(base_metrics.get("eval_loss"))
        adapter_loss = _finite(adapter_metrics.get("eval_loss"))
        if base_loss is None or adapter_loss is None:
            raise TrainingBackendError("evaluation_failed", "evaluation did not produce finite loss metrics")
        return _write_evaluation(
            context,
            _comparison_metrics(
                base_loss,
                adapter_loss,
                min(len(rows), context.dataset.validation_records),
                samples=samples,
                scorer_name=context.request.configuration.scorer_name,
            ),
        )


def _evaluate_base_and_adapter(
    *,
    adapter_model: Any,
    trainer_factory: Any,
    trainer_arguments: Any,
    eval_dataset: Any,
    data_collator: Any,
    before_adapter_evaluation: Any,
    emit: ProgressCallback,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Evaluate both variants through PEFT so quantized models remain inference-only."""
    disable_adapter = getattr(adapter_model, "disable_adapter", None)
    if not callable(disable_adapter):
        raise TrainingBackendError(
            "evaluation_failed",
            "PEFT runtime cannot disable the adapter for a controlled base comparison",
        )
    trainer = trainer_factory(
        model=adapter_model,
        args=trainer_arguments,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
    )
    with disable_adapter():
        base_metrics = trainer.evaluate()
    before_adapter_evaluation()
    emit("phase", {"phase": "evaluating_adapter"})
    adapter_metrics = trainer.evaluate()
    return base_metrics, adapter_metrics


def evaluator_for_backend(backend_name: str) -> AdapterEvaluator:
    if backend_name == "mock":
        return MockAdapterEvaluator()
    if backend_name == "unsloth":
        return PeftAdapterEvaluator(UnslothAdapterModelLoader())
    if backend_name in {"autotrain", "axolotl", "llamafactory", "peft_trl", "torchtune"}:
        return PeftAdapterEvaluator()
    raise TrainingBackendError("backend_unavailable", f"backend {backend_name} cannot evaluate adapters")


def _comparison_metrics(
    base_loss: float,
    adapter_loss: float,
    records: int,
    *,
    samples: list[dict[str, Any]] | None = None,
    scorer_name: str = "generic",
) -> dict[str, Any]:
    result = {
        "validation_records": records,
        "base": {"eval_loss": base_loss, "perplexity": _perplexity(base_loss)},
        "adapter": {"eval_loss": adapter_loss, "perplexity": _perplexity(adapter_loss)},
        "delta": {
            "eval_loss": adapter_loss - base_loss,
            "perplexity": _perplexity(adapter_loss) - _perplexity(base_loss),
        },
        "scorer_name": scorer_name,
    }
    if samples is not None:
        result["samples"] = samples[:20]
        result["wins"] = {
            "base": sum(item.get("winner") == "base" for item in samples),
            "adapter": sum(item.get("winner") == "adapter" for item in samples),
            "tie": sum(item.get("winner") == "tie" for item in samples),
        }
    return result


def _generate_comparisons(
    *,
    context: AdapterEvaluationContext,
    rows: list[dict[str, Any]],
    tokenizer: Any,
    adapter_model: Any,
    torch_module: Any,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    generation_rows = rows[: min(len(rows), context.request.configuration.max_samples, 8)]
    for index, row in enumerate(generation_rows):
        context.cancel.raise_if_cancelled()
        prompt = _prompt_text(row, tokenizer)
        reference = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        encoded = tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=context.request.configuration.max_sequence_length,
        )
        try:
            device = next(adapter_model.parameters()).device
            encoded = {key: value.to(device) for key, value in encoded.items()}
        except (AttributeError, StopIteration):
            pass
        generation = {
            "do_sample": False,
            "max_new_tokens": 64,
            "pad_token_id": tokenizer.pad_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        }
        input_length = int(encoded["input_ids"].shape[-1])
        torch_module.manual_seed(context.request.configuration.seed)
        disable_adapter = getattr(adapter_model, "disable_adapter", None)
        if not callable(disable_adapter):
            raise TrainingBackendError(
                "evaluation_failed",
                "PEFT runtime cannot disable the adapter for a controlled base comparison",
            )
        with torch_module.inference_mode(), disable_adapter():
            base_tokens = adapter_model.generate(**encoded, **generation)
        torch_module.manual_seed(context.request.configuration.seed)
        with torch_module.inference_mode():
            adapter_tokens = adapter_model.generate(**encoded, **generation)
        base_output = tokenizer.decode(base_tokens[0][input_length:], skip_special_tokens=True)[:16_000]
        adapter_output = tokenizer.decode(adapter_tokens[0][input_length:], skip_special_tokens=True)[:16_000]
        expected = _expected_output(row)[:16_000]
        base_score = score_evaluation_output(
            context.request.configuration.scorer_name,
            base_output,
            expected_output=expected,
        )
        adapter_score = score_evaluation_output(
            context.request.configuration.scorer_name,
            adapter_output,
            expected_output=expected,
        )
        samples.append(
            {
                "id": reference,
                "record_index": index,
                "base_output": base_output,
                "adapter_output": adapter_output,
                "expected_output": expected or None,
                "base_score": base_score,
                "adapter_score": adapter_score,
                "winner": _score_winner(base_score, adapter_score),
            }
        )
    return samples


def _prompt_reference(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _prompt_text(row: Mapping[str, Any], tokenizer: Any) -> str:
    if isinstance(row.get("messages"), list):
        messages = [
            dict(message)
            for message in row["messages"]
            if isinstance(message, Mapping) and str(message.get("role") or "") != "assistant"
        ]
        apply_template = getattr(tokenizer, "apply_chat_template", None)
        if callable(apply_template):
            return str(apply_template(messages, tokenize=False, add_generation_prompt=True))[:8000]
        return "\n".join(
            f"{str(message.get('role') or 'user')}: {str(message.get('content') or '')}" for message in messages
        )[:8000]
    instruction = str(row.get("instruction") or row.get("prompt") or "")
    input_text = str(row.get("input") or "")
    return (f"{instruction}\n{input_text}" if input_text else instruction)[:8000]


def _expected_output(row: Mapping[str, Any]) -> str:
    if row.get("output") is not None:
        return str(row.get("output") or "")
    messages = row.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, Mapping) and message.get("role") == "assistant":
                return str(message.get("content") or "")
    return ""


def _score_winner(base_score: Mapping[str, Any], adapter_score: Mapping[str, Any]) -> str:
    base_total = float(base_score.get("total") or 0.0)
    adapter_total = float(adapter_score.get("total") or 0.0)
    if math.isclose(base_total, adapter_total, rel_tol=0.0, abs_tol=1e-12):
        return "tie"
    return "adapter" if adapter_total > base_total else "base"


def _write_evaluation(
    context: AdapterEvaluationContext,
    metrics: Mapping[str, Any],
) -> AdapterEvaluationOutcome:
    context.artifact_root.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(metrics), sort_keys=True, separators=(",", ":"))
    compatibility_path = context.artifact_root / "evaluation.json"
    report_path = context.artifact_root / "eval_report.json"
    compatibility_path.write_text(encoded, encoding="utf-8")
    report_path.write_text(encoded, encoding="utf-8")
    return AdapterEvaluationOutcome(metrics=metrics, artifacts=(report_path, compatibility_path))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _perplexity(loss: float) -> float:
    return math.exp(min(loss, 20.0))
