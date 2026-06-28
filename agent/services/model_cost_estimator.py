"""Heuristic token and cost estimation for model routing dry-runs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.model_profile_loader import ModelProfile


_COST_CLASS_ESTIMATES = {
    "free": (0.0, 0.0),
    "very_low": (0.05, 0.10),
    "low": (0.20, 0.60),
    "medium": (1.00, 3.00),
    "high": (5.00, 15.00),
}


@dataclass
class CostEstimate:
    profile_id: str
    provider_id: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_input_cost: float
    estimated_output_cost: float
    estimated_total_cost: float
    estimated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_input_cost": self.estimated_input_cost,
            "estimated_output_cost": self.estimated_output_cost,
            "estimated_total_cost": self.estimated_total_cost,
            "estimated": self.estimated,
        }


class ModelCostEstimator:
    """Small deterministic estimator; no provider calls and no hard dependency on tokenizers."""

    def estimate_for_profile(
        self,
        profile: ModelProfile,
        *,
        prompt_text: str = "",
        expected_output_tokens: int | None = None,
    ) -> CostEstimate:
        input_tokens = self.estimate_tokens(prompt_text)
        output_tokens = int(expected_output_tokens or profile.max_output_tokens or 0)
        input_price = profile.price_input_per_million
        output_price = profile.price_output_per_million
        estimated = input_price is None or output_price is None
        if input_price is None or output_price is None:
            input_price, output_price = _COST_CLASS_ESTIMATES.get(profile.cost_class, (1.0, 3.0))
        input_cost = (input_tokens / 1_000_000.0) * float(input_price)
        output_cost = (output_tokens / 1_000_000.0) * float(output_price)
        return CostEstimate(
            profile_id=profile.profile_id,
            provider_id=profile.provider_id,
            model=profile.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_input_cost=round(input_cost, 8),
            estimated_output_cost=round(output_cost, 8),
            estimated_total_cost=round(input_cost + output_cost, 8),
            estimated=estimated,
        )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, int((len(text or "") + 3) / 4))
