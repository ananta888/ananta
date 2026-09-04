"""Bounded optimizer configurations without provider or tool authority."""

from __future__ import annotations

from typing import Any, Mapping

from ananta_contracts.dspy_optimization import OptimizationBudgets


class DspyOptimizerRegistry:
    _LIMITS = {
        "labeled_few_shot": {"max_labeled_demos": (0, 32)},
        "bootstrap_few_shot": {"max_labeled_demos": (0, 32), "max_bootstrapped_demos": (0, 16), "max_rounds": (1, 10)},
    }

    def validate(self, optimizer_id: str, config: Mapping[str, Any]) -> dict[str, int]:
        limits = self._LIMITS.get(optimizer_id)
        if limits is None:
            raise ValueError("dspy_optimizer_compatibility_error")
        if set(config) - set(limits):
            raise ValueError("dspy_optimizer_config_unknown_field")
        normalized: dict[str, int] = {}
        for key, (minimum, maximum) in limits.items():
            value = int(config.get(key, minimum))
            if not minimum <= value <= maximum:
                raise ValueError("dspy_optimizer_config_limit_invalid")
            normalized[key] = value
        return normalized

    def estimate_calls(self, optimizer_id: str, config: Mapping[str, Any], record_count: int) -> int:
        normalized = self.validate(optimizer_id, config)
        if not 0 <= record_count <= 100_000:
            raise ValueError("dspy_optimizer_record_count_invalid")
        if optimizer_id == "labeled_few_shot":
            return 0
        return min(record_count, normalized["max_bootstrapped_demos"]) * normalized["max_rounds"]

    def admit(
        self,
        optimizer_id: str,
        config: Mapping[str, Any],
        *,
        record_count: int,
        budgets: OptimizationBudgets,
    ) -> dict[str, int]:
        normalized = self.validate(optimizer_id, config)
        trials = normalized.get("max_rounds", 1)
        calls = self.estimate_calls(optimizer_id, normalized, record_count)
        if trials > budgets.max_trials:
            raise PermissionError("dspy_optimizer_trial_budget_exceeded")
        if calls > budgets.max_model_calls:
            raise PermissionError("dspy_optimizer_call_budget_exceeded")
        return {**normalized, "estimated_calls": calls, "estimated_trials": trials}


__all__ = ["DspyOptimizerRegistry"]
