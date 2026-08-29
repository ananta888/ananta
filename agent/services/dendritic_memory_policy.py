"""Default-off deterministic admission policy for dendritic experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.dendritic_memory import DendriticJobSpecV1, canonical_digest


@dataclass(frozen=True, slots=True)
class DendriticMemoryPolicy:
    enabled: bool = False
    mode: str = "disabled"
    runtime_enabled: bool = False
    automatic_activation_enabled: bool = False
    allowed_base_models: tuple[str, ...] = ()
    allowed_target_prefixes: tuple[str, ...] = ("model.layers.",)
    max_pack_bytes: int = 268_435_456
    max_active_packs: int = 4
    human_intervention_required: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticMemoryPolicy":
        allowed = set(cls.__dataclass_fields__) | {"schema"}
        if set(raw) - allowed or raw.get("schema") not in {None, "ananta.dendritic-memory-policy.v1"}:
            raise ValueError("dendritic_policy_unknown_field")
        if raw.get("human_intervention_required") not in {None, False}:
            raise ValueError("dendritic_policy_human_intervention_denied")
        value = cls(
            enabled=_strict_bool(raw.get("enabled", False), "enabled"),
            mode=str(raw.get("mode") or "disabled"),
            runtime_enabled=_strict_bool(raw.get("runtime_enabled", False), "runtime_enabled"),
            automatic_activation_enabled=_strict_bool(
                raw.get("automatic_activation_enabled", False), "automatic_activation_enabled"
            ),
            allowed_base_models=tuple(str(item) for item in raw.get("allowed_base_models", ())),
            allowed_target_prefixes=tuple(str(item) for item in raw.get("allowed_target_prefixes", ("model.layers.",))),
            max_pack_bytes=int(raw.get("max_pack_bytes", 268_435_456)),
            max_active_packs=int(raw.get("max_active_packs", 4)),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.mode not in {"disabled", "mock", "local"}:
            raise ValueError("dendritic_policy_mode_invalid")
        if self.enabled != (self.mode != "disabled"):
            raise ValueError("dendritic_policy_enabled_mode_invalid")
        if self.runtime_enabled and not self.enabled:
            raise ValueError("dendritic_runtime_requires_experiment")
        if self.automatic_activation_enabled and not self.runtime_enabled:
            raise ValueError("dendritic_automatic_activation_requires_runtime")
        if not 1_048_576 <= self.max_pack_bytes <= 2_147_483_648 or not 1 <= self.max_active_packs <= 16:
            raise ValueError("dendritic_policy_limit_invalid")
        if not self.allowed_target_prefixes or any(not value.endswith(".") for value in self.allowed_target_prefixes):
            raise ValueError("dendritic_policy_target_prefix_invalid")

    def admit(self, spec: DendriticJobSpecV1) -> None:
        if not self.enabled:
            raise PermissionError("dendritic_experiment_disabled")
        if self.allowed_base_models and spec.base_model_id not in self.allowed_base_models:
            raise PermissionError("dendritic_base_model_denied")
        if any(not target.startswith(self.allowed_target_prefixes) for target in spec.configuration.target_layers):
            raise PermissionError("dendritic_target_layer_denied")
        if spec.configuration.max_memory_bytes > self.max_pack_bytes:
            raise PermissionError("dendritic_pack_budget_exceeded")

    @property
    def digest(self) -> str:
        return canonical_digest(
            {
                "enabled": self.enabled,
                "mode": self.mode,
                "runtime_enabled": self.runtime_enabled,
                "automatic_activation_enabled": self.automatic_activation_enabled,
                "allowed_base_models": self.allowed_base_models,
                "allowed_target_prefixes": self.allowed_target_prefixes,
                "max_pack_bytes": self.max_pack_bytes,
                "max_active_packs": self.max_active_packs,
                "human_intervention_required": False,
            }
        )


def _strict_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"dendritic_policy_{field}_invalid")
    return value


__all__ = ["DendriticMemoryPolicy"]
