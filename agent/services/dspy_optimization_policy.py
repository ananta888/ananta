"""Default-off validation and registries for optimization admission."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ananta_contracts.dspy_optimization import OptimizationBudgets, OptimizationSpecV1, canonical_digest

_DEFAULT_PROGRAM_KINDS = ("planning_structured_tasks", "rag_answer", "structured_extraction")
_DEFAULT_OPTIMIZERS = ("labeled_few_shot", "bootstrap_few_shot")
_DEFAULT_RETRIEVERS = ("codecompass",)
_DEFAULT_PROVIDER_PROFILES = ("local.default",)
_DEFAULT_METRIC_SETS = ("deterministic-v1",)
_DEFAULT_BUDGETS = OptimizationBudgets(100, 500_000, 0, 1_800, 2, 10_000, 10_485_760, 2)


@dataclass(frozen=True, slots=True)
class DspyOptimizationPolicy:
    enabled: bool = False
    mode: str = "disabled"
    allow_pickle: bool = False
    allow_unsafe_lm_state: bool = False
    weight_optimization_enabled: bool = False
    external_dataset_download_enabled: bool = False
    allowed_program_kinds: tuple[str, ...] = _DEFAULT_PROGRAM_KINDS
    allowed_optimizers: tuple[str, ...] = _DEFAULT_OPTIMIZERS
    allowed_retrievers: tuple[str, ...] = _DEFAULT_RETRIEVERS
    allowed_provider_profiles: tuple[str, ...] = _DEFAULT_PROVIDER_PROFILES
    allowed_metric_sets: tuple[str, ...] = _DEFAULT_METRIC_SETS
    budgets: OptimizationBudgets = _DEFAULT_BUDGETS
    promotion_mode: str = "automatic_after_all_gates"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DspyOptimizationPolicy":
        allowed = {
            "schema",
            "enabled",
            "mode",
            "allow_pickle",
            "allow_unsafe_lm_state",
            "weight_optimization_enabled",
            "external_dataset_download_enabled",
            "allowed_program_kinds",
            "allowed_optimizers",
            "allowed_retrievers",
            "allowed_provider_profiles",
            "allowed_metric_sets",
            "budgets",
            "promotion_mode",
            "human_intervention_required",
        }
        if set(raw) - allowed or raw.get("schema") not in {None, "ananta.dspy-optimization-policy.v1"}:
            raise ValueError("dspy_policy_unknown_field")
        if any(
            bool(raw.get(key, False))
            for key in (
                "allow_pickle",
                "allow_unsafe_lm_state",
                "weight_optimization_enabled",
                "external_dataset_download_enabled",
            )
        ):
            raise ValueError("dspy_policy_unsafe_capability_denied")
        if raw.get("human_intervention_required") not in {None, False}:
            raise ValueError("dspy_policy_human_intervention_denied")
        value = cls(
            enabled=bool(raw.get("enabled", False)),
            mode=str(raw.get("mode") or "disabled"),
            allowed_program_kinds=tuple(raw.get("allowed_program_kinds") or _DEFAULT_PROGRAM_KINDS),
            allowed_optimizers=tuple(raw.get("allowed_optimizers") or _DEFAULT_OPTIMIZERS),
            allowed_retrievers=tuple(raw.get("allowed_retrievers") or _DEFAULT_RETRIEVERS),
            allowed_provider_profiles=tuple(raw.get("allowed_provider_profiles") or _DEFAULT_PROVIDER_PROFILES),
            allowed_metric_sets=tuple(raw.get("allowed_metric_sets") or _DEFAULT_METRIC_SETS),
            budgets=OptimizationBudgets(**dict(raw.get("budgets") or asdict(_DEFAULT_BUDGETS))),
            promotion_mode=str(raw.get("promotion_mode") or "automatic_after_all_gates"),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.mode not in {"disabled", "mock", "local", "cloud_gated"}:
            raise ValueError("dspy_policy_mode_invalid")
        if not self.enabled and self.mode != "disabled":
            raise ValueError("dspy_policy_disabled_mode_invalid")
        if self.enabled and self.mode == "disabled":
            raise ValueError("dspy_policy_enabled_mode_invalid")
        if not set(self.allowed_program_kinds) <= {"planning_structured_tasks", "rag_answer", "structured_extraction"}:
            raise ValueError("dspy_policy_program_kind_invalid")
        if not set(self.allowed_optimizers) <= {"labeled_few_shot", "bootstrap_few_shot"}:
            raise ValueError("dspy_policy_optimizer_invalid")
        if set(self.allowed_retrievers) - {"codecompass"}:
            raise ValueError("dspy_policy_retriever_invalid")
        if not self.allowed_provider_profiles or set(self.allowed_provider_profiles) - {"local.default", "cloud.gated"}:
            raise ValueError("dspy_policy_provider_profile_invalid")
        if not self.allowed_metric_sets or set(self.allowed_metric_sets) - {"deterministic-v1", "semantic-judge-v1"}:
            raise ValueError("dspy_policy_metric_set_invalid")
        for values in (
            self.allowed_program_kinds,
            self.allowed_optimizers,
            self.allowed_retrievers,
            self.allowed_provider_profiles,
            self.allowed_metric_sets,
        ):
            if len(values) != len(set(values)):
                raise ValueError("dspy_policy_duplicate_capability")
        if self.promotion_mode not in {"disabled", "automatic_after_all_gates"}:
            raise ValueError("dspy_policy_promotion_mode_invalid")

    @property
    def digest(self) -> str:
        return canonical_digest(asdict(self))

    def admit(self, spec: OptimizationSpecV1) -> None:
        if not self.enabled:
            raise PermissionError("dspy_optimization_disabled")
        if spec.program_kind not in self.allowed_program_kinds or spec.optimizer_id not in self.allowed_optimizers:
            raise PermissionError("dspy_optimization_capability_denied")
        requested = spec.budgets
        ceiling = self.budgets
        for field in asdict(ceiling):
            if getattr(requested, field) > getattr(ceiling, field):
                raise PermissionError("dspy_optimization_budget_exceeded")


__all__ = ["DspyOptimizationPolicy"]
