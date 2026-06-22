"""Configuration for CodeCompass candidate ranking and optional RTIPM rerank."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.codecompass_retrieval_strategy import (
    STRATEGY_DIRECT,
    ALL_STRATEGIES,
    RetrievalStrategyConfig,
)


DEFAULT_SCORE_WEIGHTS = {
    "embedding_score": 0.45,
    "graph_score": 0.20,
    "symbol_score": 0.20,
    "transformer_rerank_score": 0.0,
    "policy_penalty": -0.20,
}

# Score weights preset for strategies that enable transformer reranking.
TRANSFORMER_RERANK_WEIGHTS = {
    "embedding_score": 0.30,
    "graph_score": 0.15,
    "symbol_score": 0.15,
    "transformer_rerank_score": 0.40,
    "policy_penalty": -0.20,
}


@dataclass(frozen=True)
class CodeCompassRankingConfig:
    restricted_inference_rerank_enabled: bool = False
    score_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    trace_scores: bool = False
    fallback_without_model: bool = True
    # ── Retrieval strategy ────────────────────────────────────────────────────
    retrieval_strategy: str = STRATEGY_DIRECT
    semantic_prefilter_threshold: float = 0.25
    semantic_prefilter_top_k_multiplier: int = 2
    semantic_prefilter_min_results: int = 1

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

        strategy = str(raw.get("retrieval_strategy") or STRATEGY_DIRECT)
        if strategy not in ALL_STRATEGIES:
            strategy = STRATEGY_DIRECT

        # Auto-enable restricted reranking and adjust weights for strategies that need it.
        from agent.services.codecompass_retrieval_strategy import POSTRANK_STRATEGIES
        rerank_enabled = bool(raw.get("restricted_inference_rerank_enabled", False))
        if strategy in POSTRANK_STRATEGIES and not rerank_enabled:
            rerank_enabled = True
            if not raw.get("score_weights"):
                weights = dict(TRANSFORMER_RERANK_WEIGHTS)

        try:
            threshold = float(raw.get("semantic_prefilter_threshold") or 0.25)
        except (TypeError, ValueError):
            threshold = 0.25
        try:
            multiplier = int(raw.get("semantic_prefilter_top_k_multiplier") or 2)
        except (TypeError, ValueError):
            multiplier = 2
        try:
            min_results = int(raw.get("semantic_prefilter_min_results") or 1)
        except (TypeError, ValueError):
            min_results = 1

        return cls(
            restricted_inference_rerank_enabled=rerank_enabled,
            score_weights=weights,
            trace_scores=bool(raw.get("trace_scores", False)),
            fallback_without_model=bool(raw.get("fallback_without_model", True)),
            retrieval_strategy=strategy,
            semantic_prefilter_threshold=threshold,
            semantic_prefilter_top_k_multiplier=multiplier,
            semantic_prefilter_min_results=min_results,
        )

    def to_strategy_config(self) -> RetrievalStrategyConfig:
        return RetrievalStrategyConfig(
            strategy=self.retrieval_strategy,
            semantic_prefilter_threshold=self.semantic_prefilter_threshold,
            semantic_prefilter_top_k_multiplier=self.semantic_prefilter_top_k_multiplier,
            semantic_prefilter_min_results=self.semantic_prefilter_min_results,
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
            "retrieval_strategy": self.retrieval_strategy,
            "semantic_prefilter_threshold": self.semantic_prefilter_threshold,
            "semantic_prefilter_top_k_multiplier": self.semantic_prefilter_top_k_multiplier,
            "semantic_prefilter_min_results": self.semantic_prefilter_min_results,
            "diagnostics": self.diagnostics(),
        }


class CodeCompassRankingConfigService:
    def __init__(self, *, global_config: dict[str, Any] | None = None) -> None:
        self._global_config = dict(global_config or {})

    def resolve(self) -> CodeCompassRankingConfig:
        return CodeCompassRankingConfig.from_config(self._global_config)

    def as_dict(self) -> dict[str, Any]:
        return self.resolve().as_dict()
