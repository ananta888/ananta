"""Closed, dependency-free contract for bounded tiny-model growth experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GrowthModelConfig:
    vocab_size: int = 256
    context_length: int = 32
    width: int = 16
    heads: int = 2
    depth: int = 1
    experts: int = 1
    top_k: int = 1

    def __post_init__(self) -> None:
        for key, low, high in [
            ("vocab_size", 256, 256),
            ("context_length", 4, 256),
            ("width", 8, 256),
            ("heads", 1, 32),
            ("depth", 1, 16),
            ("experts", 1, 8),
            ("top_k", 1, 8),
        ]:
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("growth_config_invalid:" + key)
        if self.width % self.heads or self.top_k > self.experts:
            raise ValueError("growth_config_shape_invalid")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GrowthBudget:
    max_parameters: int = 2_000_000
    max_training_tokens: int = 65536
    max_training_flops: int = 5_000_000_000
    max_seconds: int = 60

    def __post_init__(self) -> None:
        for key, high in [
            ("max_parameters", 10_000_000),
            ("max_training_tokens", 1_000_000),
            ("max_training_flops", 10**13),
            ("max_seconds", 3600),
        ]:
            if type(getattr(self, key)) is not int or not 1 <= getattr(self, key) <= high:
                raise ValueError("growth_budget_invalid:" + key)


SUPPORTED_ARCHITECTURE = "ananta.byte-transformer-growth.v1"


def estimate_parameters(config: GrowthModelConfig) -> int:
    width = config.width
    return (
        2 * config.vocab_size * width
        + config.context_length * width
        + 2 * width
        + config.depth
        * (
            4 * width
            + 4 * (width * width + width)
            + config.experts * (8 * width * width + 5 * width)
            + width * config.experts
        )
    )
