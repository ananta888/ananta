"""Bounded reference training port; no downloads, orchestration or promotion."""

from __future__ import annotations

import math
import time
from typing import Protocol

from ananta_contracts.adaptive_growth import GrowthBudget
from worker.training.research.modeling import require_torch


class GrowthTrainer(Protocol):
    def train(
        self, model, optimizer, *, tokens: tuple[int, ...], steps: int, budget: GrowthBudget, start_step: int = 0
    ) -> dict: ...


def parameters(model) -> tuple[int, int]:
    total = sum(p.numel() for p in model.parameters())
    expert_params = sum(p.numel() for b in model.blocks for e in b.experts for p in e.parameters())
    active = total - expert_params + expert_params * model.config.top_k // model.config.experts
    return total, active


def estimated_step_flops(model, tokens: int) -> int:
    # Parameter matmuls plus causal attention; an explicit estimate, not hardware
    # FLOP telemetry. All comparison arms use this same published convention.
    return 6 * parameters(model)[1] * tokens + 12 * model.config.depth * model.config.width * tokens**2


def windows(tokens: tuple[int, ...], context: int):
    if len(tokens) < context + 1:
        raise ValueError("growth_dataset_too_small")
    return [tokens[i : i + context + 1] for i in range(0, len(tokens) - context, context)]


def window_index(step: int, count: int) -> int:
    """Stable full-corpus traversal, independent of arm, run length and resume."""
    if type(step) is not int or step < 0 or type(count) is not int or count < 1:
        raise ValueError("growth_window_schedule_invalid")
    stride = max(1, int(count * 0.6180339887498949))
    while math.gcd(stride, count) != 1:
        stride += 1
    return step * stride % count


class TorchGrowthTrainer:
    def train(
        self, model, optimizer, *, tokens: tuple[int, ...], steps: int, budget: GrowthBudget, start_step: int = 0
    ) -> dict:
        torch = require_torch()
        if type(steps) is not int or not 1 <= steps <= 10000 or type(start_step) is not int or start_step < 0:
            raise ValueError("growth_steps_invalid")
        if parameters(model)[0] > budget.max_parameters:
            raise ValueError("growth_parameter_budget_exceeded")
        chunks = windows(tokens, model.config.context_length)
        device = next(model.parameters()).device
        model.train()
        started = time.monotonic()
        used_tokens, used_flops, completed = 0, 0, 0
        losses, gradients = [], []
        cost = estimated_step_flops(model, model.config.context_length)
        for step in range(start_step, start_step + steps):
            if (
                used_tokens + model.config.context_length > budget.max_training_tokens
                or used_flops + cost > budget.max_training_flops
                or time.monotonic() - started >= budget.max_seconds
            ):
                break
            chunk = chunks[window_index(step, len(chunks))]
            x = torch.tensor([chunk[:-1]], device=device)
            y = torch.tensor(chunk[1:], device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(x).reshape(-1, 256), y)
            objective = loss + 0.01 * model.auxiliary_loss()
            if not torch.isfinite(objective):
                raise ValueError("growth_training_nonfinite")
            objective.backward()
            gradient = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            if not torch.isfinite(gradient):
                raise ValueError("growth_gradient_nonfinite")
            optimizer.step()
            losses.append(float(loss.detach()))
            gradients.append(float(gradient))
            used_tokens += model.config.context_length
            used_flops += cost
            completed += 1
        if not completed:
            raise ValueError("growth_budget_exhausted")
        elapsed = time.monotonic() - started
        utilization = [b.last_utilization.cpu().tolist() for b in model.blocks]
        return {
            "steps": completed,
            "sampling_policy": "coprime_full_corpus_stride.v1",
            "available_windows": len(chunks),
            "tokens": used_tokens,
            "estimated_training_flops": used_flops,
            "flops_kind": "analytical_estimate",
            "seconds": elapsed,
            "tokens_per_second": used_tokens / max(elapsed, 1e-9),
            "losses": losses,
            "gradient_norms": gradients,
            "total_parameters": parameters(model)[0],
            "active_parameters": parameters(model)[1],
            "parameter_bytes": sum(p.numel() * p.element_size() for p in model.parameters()),
            "optimizer_bytes": sum(
                v.numel() * v.element_size()
                for s in optimizer.state.values()
                for v in s.values()
                if isinstance(v, torch.Tensor)
            ),
            "expert_utilization": utilization,
            "expert_collapse": any(min(values) == 0 for values in utilization),
            "peak_vram_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
        }


def evaluate(model, tokens: tuple[int, ...]) -> float:
    torch = require_torch()
    chunks = windows(tokens, model.config.context_length)
    device = next(model.parameters()).device
    model.eval()
    values = []
    with torch.inference_mode():
        for index in range(min(32, len(chunks))):
            chunk = chunks[window_index(index, len(chunks))]
            x = torch.tensor([chunk[:-1]], device=device)
            y = torch.tensor(chunk[1:], device=device)
            values.append(float(torch.nn.functional.cross_entropy(model(x).reshape(-1, 256), y)))
    result = sum(values) / len(values)
    if not math.isfinite(result):
        raise ValueError("growth_evaluation_nonfinite")
    return result
