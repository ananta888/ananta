"""Configuration for CodeCompass candidate ranking and optional RTIPM rerank."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from typing import Any

from agent.services.codecompass_retrieval_strategy import (
    ALL_STRATEGIES,
    STRATEGY_SEMANTIC_PREFILTER,
    RetrievalStrategyConfig,
)
from ananta_codecompass.ranking.profiles import (
    HYBRID_SCORE_WEIGHTS,
    HYBRID_TRANSFORMER_WEIGHTS,
)

DEFAULT_SCORE_WEIGHTS = dict(HYBRID_SCORE_WEIGHTS)

# Score weights preset for strategies that enable transformer reranking.
TRANSFORMER_RERANK_WEIGHTS = dict(HYBRID_TRANSFORMER_WEIGHTS)


@dataclass(frozen=True)
class CodeCompassRankingConfig:
    restricted_inference_rerank_enabled: bool = True
    score_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    trace_scores: bool = False
    fallback_without_model: bool = True
    restricted_inference_max_candidates: int = 20
    # ── Retrieval strategy ────────────────────────────────────────────────────
    retrieval_strategy: str = STRATEGY_SEMANTIC_PREFILTER
    semantic_prefilter_threshold: float = 0.25
    semantic_prefilter_top_k_multiplier: int = 2
    semantic_prefilter_min_results: int = 1
    override_status: str = "disabled"
    override_metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "CodeCompassRankingConfig":
        top = dict(config or {})
        raw = dict(top.get("codecompass_ranking") or {})
        # Bridge top-level chat_retrieval_strategy into codecompass_ranking when not
        # configured explicitly via Config Graph (avoids file I/O in the orchestrator).
        if not raw.get("retrieval_strategy") and top.get("chat_retrieval_strategy"):
            raw["retrieval_strategy"] = top["chat_retrieval_strategy"]
        weights = dict(DEFAULT_SCORE_WEIGHTS)
        override_metadata = dict(raw.get("override_metadata") or {})
        required_override_fields = {"owner", "reason", "scope", "version", "expires_at"}
        override_status = "disabled"
        governed_override = False
        if raw.get("score_weights"):
            if required_override_fields <= set(override_metadata):
                try:
                    expires = dt.datetime.fromisoformat(
                        str(override_metadata["expires_at"]).replace("Z", "+00:00")
                    )
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=dt.timezone.utc)
                    governed_override = expires > dt.datetime.now(dt.timezone.utc)
                    override_status = "active_experimental_override" if governed_override else "rejected_expired"
                except ValueError:
                    override_status = "rejected_invalid_expiry"
            else:
                override_status = "rejected_missing_governance"
        for key, value in dict(raw.get("score_weights") or {}).items() if governed_override else ():
            if key not in weights:
                continue
            try:
                weights[key] = float(value)
            except (TypeError, ValueError):
                continue

        strategy = str(raw.get("retrieval_strategy") or STRATEGY_SEMANTIC_PREFILTER)
        if strategy not in ALL_STRATEGIES:
            strategy = STRATEGY_SEMANTIC_PREFILTER

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
        try:
            max_candidates = max(1, min(64, int(raw.get("restricted_inference_max_candidates") or 20)))
        except (TypeError, ValueError):
            max_candidates = 20

        return cls(
            restricted_inference_rerank_enabled=rerank_enabled,
            score_weights=weights,
            trace_scores=bool(raw.get("trace_scores", False)),
            fallback_without_model=bool(raw.get("fallback_without_model", True)),
            restricted_inference_max_candidates=max_candidates,
            retrieval_strategy=strategy,
            semantic_prefilter_threshold=threshold,
            semantic_prefilter_top_k_multiplier=multiplier,
            semantic_prefilter_min_results=min_results,
            override_status=override_status,
            override_metadata={key: str(value) for key, value in override_metadata.items()} if governed_override else {},
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
            "restricted_inference_max_candidates": self.restricted_inference_max_candidates,
            "retrieval_strategy": self.retrieval_strategy,
            "semantic_prefilter_threshold": self.semantic_prefilter_threshold,
            "semantic_prefilter_top_k_multiplier": self.semantic_prefilter_top_k_multiplier,
            "semantic_prefilter_min_results": self.semantic_prefilter_min_results,
            "override_status": self.override_status,
            "override_metadata": dict(self.override_metadata),
            "diagnostics": self.diagnostics(),
        }


class CodeCompassRankingConfigService:
    def __init__(self, *, global_config: dict[str, Any] | None = None) -> None:
        self._global_config = dict(global_config or {})

    def resolve(self) -> CodeCompassRankingConfig:
        return CodeCompassRankingConfig.from_config(self._global_config)

    def as_dict(self) -> dict[str, Any]:
        return self.resolve().as_dict()
