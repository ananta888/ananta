"""Closed recipe registry for the pinned torchtune adapter."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from worker.training.backends.base import TrainingBackendError

_RECIPES: Mapping[str, str] = MappingProxyType(
    {
        "llama3_2_3b_lora_single_device": "lora_finetune_single_device",
    }
)


def require_recipe(profile: str) -> str:
    try:
        return _RECIPES[profile]
    except KeyError as exc:
        raise TrainingBackendError("config_invalid", "torchtune recipe profile is not allowlisted") from exc


def recipes() -> Mapping[str, str]:
    return _RECIPES


__all__ = ["recipes", "require_recipe"]
