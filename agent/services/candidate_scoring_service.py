"""CodeCompass candidate scoring with optional trace output."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from agent.services.codecompass_ranking_config_service import CodeCompassRankingConfig


@dataclass(frozen=True)
class CandidateScoreTrace:
    embedding_score: float = 0.0
    graph_score: float = 0.0
    symbol_score: float = 0.0
    transformer_rerank_score: float = 0.0
    policy_penalty: float = 0.0
    final_score: float = 0.0
    policy_reason: str = ""
    model_id: str = ""
    engine: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "embedding_score": self.embedding_score,
            "graph_score": self.graph_score,
            "symbol_score": self.symbol_score,
            "transformer_rerank_score": self.transformer_rerank_score,
            "policy_penalty": self.policy_penalty,
            "final_score": self.final_score,
            "policy_reason": self.policy_reason,
            "model_id": self.model_id,
            "engine": self.engine,
        }


@dataclass(frozen=True)
class RankedCandidate:
    path: str
    record_id: str
    excerpt: str = ""
    symbols: list[str] = field(default_factory=list)
    final_score: float = 0.0
    trace: CandidateScoreTrace = field(default_factory=CandidateScoreTrace)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, include_trace: bool = False) -> dict[str, Any]:
        payload = dict(self.raw)
        payload.update({
            "path": self.path,
            "record_id": self.record_id,
            "final_score": self.final_score,
        })
        if include_trace:
            payload["score_trace"] = self.trace.as_dict()
        return payload


class CandidateScoringService:
    """Scores CodeCompass/RAG candidates using deterministic weighted sums."""

    def __init__(self, *, config: CodeCompassRankingConfig | None = None) -> None:
        self._config = config or CodeCompassRankingConfig()

    def rank(
        self,
        candidates: list[dict[str, Any]],
        *,
        transformer_scores: dict[str, dict[str, Any]] | None = None,
    ) -> list[RankedCandidate]:
        scores = dict(transformer_scores or {})
        ranked = [self._rank_one(candidate, scores.get(_candidate_key(candidate))) for candidate in candidates]
        ranked.sort(key=lambda item: (-item.final_score, item.path, item.record_id))
        return ranked

    def _rank_one(self, candidate: dict[str, Any], transformer: dict[str, Any] | None) -> RankedCandidate:
        path = str(candidate.get("path") or candidate.get("source") or "")
        record_id = str(candidate.get("record_id") or candidate.get("id") or _stable_id(path))
        embedding = _clamp(candidate.get("embedding_score") or candidate.get("score") or 0.0)
        graph = _clamp(candidate.get("graph_score") or candidate.get("graph_distance_score") or 0.0)
        symbol = _clamp(candidate.get("symbol_score") or candidate.get("symbol_match_score") or 0.0)
        policy = _penalty(candidate.get("policy_penalty") or 0.0)
        transformer_score = _clamp((transformer or {}).get("score") or 0.0)
        weights = self._config.score_weights
        final = (
            embedding * weights["embedding_score"]
            + graph * weights["graph_score"]
            + symbol * weights["symbol_score"]
            + transformer_score * weights["transformer_rerank_score"]
            + policy * abs(weights["policy_penalty"])
        )
        final_score = round(max(0.0, min(1.0, final)), 6)
        trace = CandidateScoreTrace(
            embedding_score=embedding,
            graph_score=graph,
            symbol_score=symbol,
            transformer_rerank_score=transformer_score,
            policy_penalty=policy,
            final_score=final_score,
            policy_reason=str(candidate.get("policy_reason") or candidate.get("reason") or ""),
            model_id=str((transformer or {}).get("model_id") or ""),
            engine=str((transformer or {}).get("engine") or ""),
        )
        return RankedCandidate(
            path=path,
            record_id=record_id,
            excerpt=str(candidate.get("excerpt") or "")[:500],
            symbols=[str(item) for item in (candidate.get("symbols") or [])],
            final_score=final_score,
            trace=trace,
            raw=dict(candidate),
        )


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("record_id") or candidate.get("id") or candidate.get("path") or candidate.get("source") or "")


def _stable_id(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()[:12]


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _penalty(value: Any) -> float:
    try:
        return min(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0
