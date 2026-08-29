"""Lazy Torch reference component; importing this module does not import Torch."""

from __future__ import annotations

from typing import Any


def build_dendritic_memory_module(
    *,
    hidden_dimension: int,
    branch_count: int,
    top_k: int,
    routing_enabled: bool,
    readout: str,
    max_memory_bytes: int,
):
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("dendritic_torch_unavailable") from exc
    parameter_bytes = branch_count * hidden_dimension * 2 * 4
    if parameter_bytes > max_memory_bytes:
        raise ValueError("dendritic_module_memory_budget_exceeded")

    class DendriticMemoryModule(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.branch_keys = nn.Parameter(torch.empty(branch_count, hidden_dimension))
            self.branch_values = nn.Parameter(torch.empty(branch_count, hidden_dimension))
            self.gate = nn.Parameter(torch.zeros(())) if readout == "gated_residual" else None
            nn.init.normal_(self.branch_keys, mean=0.0, std=hidden_dimension**-0.5)
            nn.init.zeros_(self.branch_values)

        def forward(self, value):
            if value.ndim not in {2, 3} or value.shape[-1] != hidden_dimension:
                raise ValueError("dendritic_forward_shape_invalid")
            if not torch.isfinite(value).all():
                raise ValueError("dendritic_forward_non_finite_input")
            scores = torch.einsum("...h,bh->...b", value.float(), self.branch_keys.float())
            if routing_enabled:
                selected_scores, selected = torch.topk(scores, k=top_k, dim=-1)
                weights = torch.softmax(selected_scores, dim=-1)
                selected_values = self.branch_values[selected]
                memory = torch.einsum("...k,...kh->...h", weights, selected_values)
            else:
                memory = self.branch_values.mean(dim=0).expand_as(value)
            scale = torch.sigmoid(self.gate) if self.gate is not None else 1.0
            result = value + memory.to(dtype=value.dtype) * scale
            if not torch.isfinite(result).all():
                raise RuntimeError("dendritic_forward_non_finite_output")
            return result

    return DendriticMemoryModule()


def parameter_report(module: Any) -> dict[str, int]:
    try:
        parameters = tuple(module.parameters())
    except (AttributeError, TypeError) as exc:
        raise ValueError("dendritic_module_invalid") from exc
    return {
        "parameter_count": sum(int(item.numel()) for item in parameters),
        "trainable_parameter_count": sum(int(item.numel()) for item in parameters if item.requires_grad),
    }


__all__ = ["build_dendritic_memory_module", "parameter_report"]
