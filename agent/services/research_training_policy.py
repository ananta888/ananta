"""Default-off admission policy for full-model research training."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.research_training import ResearchRunSpecV1, require_id


@dataclass(frozen=True, slots=True)
class ResearchTrainingPolicy:
    enabled: bool
    mode: str
    automatic_release_enabled: bool
    allowed_model_families: frozenset[str]
    max_gpu_hours: float
    max_storage_bytes: int
    max_estimated_cost_microunits: int
    max_world_size: int
    max_stages: int
    max_artifact_bytes: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTrainingPolicy:
        expected = {
            "schema",
            "enabled",
            "mode",
            "automatic_release_enabled",
            "allowed_model_families",
            "max_gpu_hours",
            "max_storage_bytes",
            "max_estimated_cost_microunits",
            "max_world_size",
            "max_stages",
            "max_artifact_bytes",
            "human_intervention_required",
        }
        if set(value) != expected or value.get("schema") != "ananta.research-training-policy.v1":
            raise ValueError("research_policy_fields_invalid")
        if any(
            not isinstance(value.get(field), bool)
            for field in ("enabled", "automatic_release_enabled", "human_intervention_required")
        ):
            raise ValueError("research_policy_boolean_invalid")
        if value.get("human_intervention_required") is not False:
            raise ValueError("research_human_intervention_forbidden")
        mode = str(value.get("mode") or "").strip().lower()
        if mode not in {"disabled", "mock", "local"}:
            raise ValueError("research_policy_mode_invalid")
        raw_families = value.get("allowed_model_families")
        if not isinstance(raw_families, list) or len(raw_families) > 64:
            raise ValueError("research_allowed_model_families_invalid")
        families = frozenset(require_id(item, "model_family") for item in raw_families)
        policy = cls(
            enabled=bool(value["enabled"]),
            mode=mode,
            automatic_release_enabled=bool(value["automatic_release_enabled"]),
            allowed_model_families=families,
            max_gpu_hours=float(value["max_gpu_hours"]),
            max_storage_bytes=int(value["max_storage_bytes"]),
            max_estimated_cost_microunits=int(value["max_estimated_cost_microunits"]),
            max_world_size=int(value["max_world_size"]),
            max_stages=int(value["max_stages"]),
            max_artifact_bytes=int(value["max_artifact_bytes"]),
        )
        policy._validate()
        return policy

    def _validate(self) -> None:
        if not 0 <= self.max_gpu_hours <= 100_000:
            raise ValueError("research_max_gpu_hours_invalid")
        if not 1 <= self.max_storage_bytes <= 1 << 50:
            raise ValueError("research_max_storage_invalid")
        if not 0 <= self.max_estimated_cost_microunits <= 10**15:
            raise ValueError("research_max_cost_invalid")
        if not 1 <= self.max_world_size <= 1024 or not 1 <= self.max_stages <= 32:
            raise ValueError("research_policy_limit_invalid")
        if not 1 <= self.max_artifact_bytes <= self.max_storage_bytes:
            raise ValueError("research_max_artifact_invalid")
        if not self.enabled and self.mode != "disabled":
            raise ValueError("research_disabled_mode_invalid")
        if self.enabled and self.mode == "disabled":
            raise ValueError("research_enabled_mode_invalid")
        if self.automatic_release_enabled and not self.enabled:
            raise ValueError("research_automatic_release_requires_enabled")

    def denial_reasons(self, spec: ResearchRunSpecV1) -> list[str]:
        reasons: list[str] = []
        if not self.enabled:
            reasons.append("research_training_disabled")
        if self.allowed_model_families and spec.recipe.model_family not in self.allowed_model_families:
            reasons.append("research_model_family_denied")
        if spec.recipe.world_size > self.max_world_size:
            reasons.append("research_world_size_exceeded")
        if len(spec.pipeline.stages) > self.max_stages:
            reasons.append("research_stage_limit_exceeded")
        if spec.budget.gpu_hours > self.max_gpu_hours:
            reasons.append("research_gpu_budget_exceeded")
        if spec.budget.storage_bytes > self.max_storage_bytes:
            reasons.append("research_storage_budget_exceeded")
        if spec.budget.estimated_cost_microunits > self.max_estimated_cost_microunits:
            reasons.append("research_cost_budget_exceeded")
        if any(stage.kind == "rl" for stage in spec.pipeline.stages) and not spec.recipe.allow_rl:
            reasons.append("research_rl_not_enabled")
        return reasons


__all__ = ["ResearchTrainingPolicy"]
