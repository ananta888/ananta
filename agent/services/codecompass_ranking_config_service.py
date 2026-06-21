"""Configuration for CodeCompass candidate ranking and optional RTIPM rerank."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_SCORE_WEIGHTS = {
    "embedding_score": 0.45,
    "graph_score": 0.20,
    "symbol_score": 0.20,
    "transformer_rerank_score": 0.0,
    "policy_penalty": -0.20,
}


@dataclass(frozen=True)
class CodeCompassRankingConfig:
    restricted_inference_rerank_enabled: bool = False
    score_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    trace_scores: bool = False
    fallback_without_model: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "CodeCompassRankingConfig":
        raw = dict((config or {}).get("codecompass_ranking") or {})
        weights = dict(DEFAULT_SCORE_WEIGHTS)
        for key, value in dict(raw.get("score_weights") or {}).items():
            if key not in weights:
                continue
            try:
                weights[key] = float(value)
            except (TypeError, ValueError):
                continue
        return cls(
            restricted_inference_rerank_enabled=bool(raw.get("restricted_inference_rerank_enabled", False)),
            score_weights=weights,
            trace_scores=bool(raw.get("trace_scores", False)),
            fallback_without_model=bool(raw.get("fallback_without_model", True)),
        )

    def diagnostics(self) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        for key, value in self.score_weights.items():
            if not isinstance(value, float | int):
                diagnostics.append({"reason_code": "invalid_weight", "field": key})
            elif key != "policy_penalty" and value < 0:
                diagnostics.append({"reason_code": "invalid_weight", "field": key})
            elif abs(float(value)) > 10:
                diagnostics.append({"reason_code": "invalid_weight", "field": key})
        return diagnostics

    def as_dict(self) -> dict[str, Any]:
        return {
            "restricted_inference_rerank_enabled": self.restricted_inference_rerank_enabled,
            "score_weights": dict(self.score_weights),
            "trace_scores": self.trace_scores,
            "fallback_without_model": self.fallback_without_model,
            "diagnostics": self.diagnostics(),
        }


class CodeCompassRankingConfigService:
    def __init__(self, *, global_config: dict[str, Any] | None = None) -> None:
        self._global_config = dict(global_config or {})

    def resolve(self) -> CodeCompassRankingConfig:
        return CodeCompassRankingConfig.from_config(self._global_config)

    def as_dict(self) -> dict[str, Any]:
        return self.resolve().as_dict()
