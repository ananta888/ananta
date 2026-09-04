"""Deterministic PyTorch training and evaluation primitives.

This module contains execution mechanics only.  It does not decide admission,
schedule stages, publish artifacts, or promote models.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worker.training.research.modeling import TinyCausalLmConfig, build_model, require_torch
from worker.training.research.preemption import ResearchTrainingPreempted
from worker.training.research.text_data import record_text, supervised_tokens
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    model: Any
    metrics: Mapping[str, float]
    optimizer_steps: int


def config_from_recipe(recipe: Mapping[str, Any], *, tokenizer_vocab_size: int) -> TinyCausalLmConfig:
    resolved = recipe.get("resolved_hyperparameters")
    if not isinstance(resolved, Mapping):
        raise ValueError("research_recipe_hyperparameters_invalid")
    configured_vocab = int(recipe["vocab_size"])
    if configured_vocab < tokenizer_vocab_size:
        raise ValueError("research_model_tokenizer_vocab_mismatch")
    config = TinyCausalLmConfig(
        vocab_size=configured_vocab,
        context_length=int(recipe["context_length"]),
        hidden_size=int(resolved["hidden_size"]),
        num_layers=int(resolved["num_layers"]),
        attention_heads=int(resolved["attention_heads"]),
        dropout=float(resolved.get("dropout", 0.0)),
    )
    config.validate()
    return config


class TorchLanguageModelTrainer:
    def train(
        self,
        *,
        records: Sequence[Mapping[str, Any]],
        tokenizer: ByteBpeTokenizer,
        config: TinyCausalLmConfig,
        max_steps: int,
        seed: int,
        learning_rate: float,
        weight_decay: float,
        gradient_clip: float,
        precision: str,
        parent_model: Any | None = None,
        supervised: bool = False,
        checkpoint_cadence: int = 0,
        checkpoint_callback: Callable[[Any, int], None] | None = None,
        preemption_requested: Callable[[], bool] | None = None,
        initial_step: int = 0,
    ) -> TrainingOutcome:
        torch = require_torch()
        if (
            not records
            or not 1 <= max_steps <= 100_000_000
            or not isinstance(initial_step, int)
            or isinstance(initial_step, bool)
            or not 0 <= initial_step < max_steps
        ):
            raise ValueError("research_training_steps_invalid")
        if precision not in {"float32", "float16", "bfloat16"}:
            raise ValueError("research_training_precision_invalid")
        seed_everything(seed)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if device.type == "cpu" and precision != "float32":
            raise ValueError("research_training_precision_device_mismatch")
        model = parent_model if parent_model is not None else build_model(config)
        model.to(device)
        distributed = torch.distributed.is_available() and torch.distributed.is_initialized()
        if distributed:
            model = torch.nn.parallel.DistributedDataParallel(
                model,
                device_ids=[torch.cuda.current_device()] if device.type == "cuda" else None,
            )
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        examples = self._examples(records, tokenizer, config.context_length, supervised=supervised)
        losses: list[float] = []
        started = time.perf_counter()
        scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda" and precision == "float16")
        autocast_dtype = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }[precision]
        for step in range(initial_step, max_steps):
            tokens, targets, mask = examples[step % len(examples)]
            inputs = torch.tensor([tokens], dtype=torch.long, device=device)
            labels = torch.tensor([targets], dtype=torch.long, device=device)
            weights = torch.tensor([mask], dtype=torch.float32, device=device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(
                device_type=device.type,
                dtype=autocast_dtype,
                enabled=precision != "float32",
            ):
                logits = model(inputs)
                element_loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, config.vocab_size),
                    labels.reshape(-1),
                    reduction="none",
                ).reshape_as(weights)
                denominator = weights.sum().clamp_min(1.0)
                loss = (element_loss * weights).sum() / denominator
            if not torch.isfinite(loss):
                raise ValueError("research_training_loss_non_finite")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
            if not torch.isfinite(gradient_norm):
                raise ValueError("research_training_gradient_non_finite")
            scaler.step(optimizer)
            scaler.update()
            losses.append(float(loss.detach().cpu()))
            optimizer_step = step + 1
            should_checkpoint = bool(
                checkpoint_callback
                and checkpoint_cadence > 0
                and optimizer_step % checkpoint_cadence == 0
            )
            should_preempt = bool(preemption_requested and preemption_requested())
            if checkpoint_callback and (should_checkpoint or should_preempt):
                checkpoint_callback(model.module if distributed else model, optimizer_step)
            if should_preempt:
                raise ResearchTrainingPreempted(optimizer_step)
        elapsed = max(time.perf_counter() - started, 1e-9)
        raw_model = model.module if distributed else model
        executed_steps = max_steps - initial_step
        token_count = sum(len(example[0]) for example in examples) * executed_steps / len(examples)
        peak_memory = (
            float(torch.cuda.max_memory_allocated(device))
            if device.type == "cuda"
            else 0.0
        )
        metrics = {
            "loss": losses[-1],
            "initial_loss": losses[0],
            "minimum_loss": min(losses),
            "throughput_tokens_s": token_count / elapsed,
            "elapsed_seconds": elapsed,
            "peak_memory_bytes": peak_memory,
            "optimizer_steps": float(max_steps),
            "resumed_from_optimizer_step": float(initial_step),
            "world_size": float(torch.distributed.get_world_size() if distributed else 1),
            "supervised_loss_mask": float(supervised),
        }
        return TrainingOutcome(raw_model, metrics, max_steps)

    @staticmethod
    def _examples(
        records: Sequence[Mapping[str, Any]],
        tokenizer: ByteBpeTokenizer,
        context_length: int,
        *,
        supervised: bool,
    ) -> list[tuple[list[int], list[int], list[float]]]:
        examples: list[tuple[list[int], list[int], list[float]]] = []
        for record in records:
            if supervised:
                prepared = supervised_tokens(record, tokenizer)
                identifiers = list(prepared.token_ids)
                raw_mask = list(prepared.loss_mask)
            else:
                identifiers = tokenizer.encode(record_text(record))
                raw_mask = [True] * len(identifiers)
            for start in range(0, max(1, len(identifiers) - 1), context_length):
                window = identifiers[start : start + context_length + 1]
                if len(window) < 2:
                    continue
                mask = raw_mask[start + 1 : start + len(window)]
                if not any(mask):
                    continue
                examples.append((window[:-1], window[1:], [float(value) for value in mask]))
        if not examples:
            raise ValueError("research_training_examples_empty")
        return examples


class TorchLanguageModelEvaluator:
    def evaluate(
        self,
        *,
        model: Any,
        config: TinyCausalLmConfig,
        tokenizer: ByteBpeTokenizer,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, float]:
        torch = require_torch()
        examples = TorchLanguageModelTrainer._examples(
            records, tokenizer, config.context_length, supervised=False
        )
        device = next(model.parameters()).device
        model.eval()
        losses: list[float] = []
        bytes_total = 0
        tokens_total = 0
        with torch.inference_mode():
            for tokens, targets, _ in examples:
                inputs = torch.tensor([tokens], dtype=torch.long, device=device)
                labels = torch.tensor([targets], dtype=torch.long, device=device)
                logits = model(inputs)
                loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, config.vocab_size), labels.reshape(-1)
                )
                losses.append(float(loss.detach().cpu()))
                tokens_total += len(targets)
        for record in records:
            bytes_total += len(record_text(record).encode("utf-8"))
        loss = sum(losses) / len(losses)
        bits_per_byte = loss / math.log(2) * tokens_total / max(1, bytes_total)
        return {
            "loss": loss,
            "bits_per_byte": bits_per_byte,
            "perplexity": math.exp(min(loss, 80.0)),
            "tokens": float(tokens_total),
            "bytes": float(bytes_total),
        }


def seed_everything(seed: int) -> None:
    torch = require_torch()
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)


__all__ = [
    "TorchLanguageModelEvaluator",
    "TorchLanguageModelTrainer",
    "TrainingOutcome",
    "config_from_recipe",
    "seed_everything",
]
