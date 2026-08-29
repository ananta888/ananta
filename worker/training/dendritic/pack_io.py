"""Safetensors-only weight serialization for executable Memory Packs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DendriticSafetensorsPackIo:
    def dump(self, state: Mapping[str, Any]) -> bytes:
        self._validate_keys(state)
        try:
            from safetensors.torch import save
        except ImportError as exc:
            raise RuntimeError("dendritic_safetensors_unavailable") from exc
        return save(dict(state))

    def load(self, payload: bytes) -> dict[str, Any]:
        if not payload:
            raise ValueError("dendritic_safetensors_empty")
        try:
            from safetensors.torch import load
        except ImportError as exc:
            raise RuntimeError("dendritic_safetensors_unavailable") from exc
        value = dict(load(payload))
        self._validate_keys(value)
        return value

    @staticmethod
    def _validate_keys(state: Mapping[str, Any]) -> None:
        if not 1 <= len(state) <= 256:
            raise ValueError("dendritic_state_size_invalid")
        if any(
            not isinstance(key, str)
            or not key.startswith("memory.")
            or ".." in key
            or len(key) > 192
            for key in state
        ):
            raise ValueError("dendritic_state_key_invalid")


__all__ = ["DendriticSafetensorsPackIo"]
