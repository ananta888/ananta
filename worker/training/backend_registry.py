"""Startup-frozen registry and v3 capability projection for training workers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

from ananta_contracts.training_backend import BACKEND_IDS, TrainingBackendCapability
from worker.training.backends.base import TrainingBackend, TrainingBackendError


class FrozenTrainingBackendRegistry(Mapping[str, TrainingBackend]):
    def __init__(self, backends: Iterable[TrainingBackend]) -> None:
        values: dict[str, TrainingBackend] = {}
        for backend in backends:
            name = str(backend.name or "").strip().lower()
            if name not in BACKEND_IDS or name in values:
                raise ValueError("training backend names must be allowlisted and unique")
            values[name] = backend
        self._backends: Mapping[str, TrainingBackend] = MappingProxyType(values)

    def __getitem__(self, key: str) -> TrainingBackend:
        return self._backends[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._backends)

    def __len__(self) -> int:
        return len(self._backends)

    def require(self, name: str) -> TrainingBackend:
        try:
            return self._backends[name]
        except KeyError as exc:
            raise TrainingBackendError("backend_unavailable", "training backend is not registered") from exc

    def capabilities(self) -> dict[str, dict[str, Any]]:
        projected: dict[str, dict[str, Any]] = {}
        for name, backend in self._backends.items():
            provider = getattr(backend, "capability", None)
            if callable(provider):
                capability = provider()
                if not isinstance(capability, TrainingBackendCapability) or capability.backend_id != name:
                    raise TrainingBackendError("config_invalid", "backend capability projection is inconsistent")
                projected[name] = capability.to_dict()
                continue
            available, _detail = backend.availability()
            payload = {
                "schema_version": "ananta.training-backend-capability.v3",
                "backend_id": name,
                "backend_version": "legacy-v1",
                "available": available,
                "reason_code": "ok" if available else "dependency_unavailable",
                "maturity": "production" if name in {"mock", "peft_trl", "unsloth"} else "experimental",
                "maintenance": "active",
                "license_spdx": "internal-adapter",
                "modalities": [_legacy_modality(name)],
                "objectives": ["sft"],
                "methods": ["lora", "qlora"] if name != "mock" else ["lora"],
                "precisions": ["bf16", "fp16", "fp32"],
                "quantizations": ["4bit", "none"],
                "distributed_modes": ["single_device"],
                "exports": ["adapter"],
                "resume": True,
                "evaluation": name in {"mock", "peft_trl", "unsloth"},
                "resource_profiles": ["cpu", "generic-safe", "rtx3080-safe"],
            }
            projected[name] = TrainingBackendCapability.from_mapping(payload).to_dict()
        return projected


def _legacy_modality(name: str) -> str:
    for modality in ("audio", "embedding", "vision"):
        if modality in name:
            return modality
    return "text"


__all__ = ["FrozenTrainingBackendRegistry"]
