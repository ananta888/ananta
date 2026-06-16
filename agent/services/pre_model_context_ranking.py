"""APMCO-004: Candidate scoring and ranking for pre-model context.

Combines embedding score, symbol match, graph distance, working-file bonus,
domain scope bonus, test relation, recency, policy penalty and sensitivity
penalty into a single deterministic final score.

Tie-breaking: final_score desc → path asc → record_id asc.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from agent.services.pre_model_context_config import RankingWeights


@dataclass
class ScoredCandidate:
    """Single ranked candidate with full score breakdown (APMCO-004 trace)."""
    path: str
    record_id: str
    excerpt: str = ""
    symbols: list[str] = field(default_factory=list)

    # Raw score components (0.0 – 1.0 each, except penalties which are ≤ 0)
    embedding_score: float = 0.0
    symbol_match_score: float = 0.0
    graph_distance_score: float = 0.0
    working_file_bonus: float = 0.0
    domain_scope_bonus: float = 0.0
    test_relation_bonus: float = 0.0
    recency_bonus: float = 0.0
    policy_penalty: float = 0.0       # negative value when policy flags candidate
    sensitivity_penalty: float = 0.0  # negative value for sensitive paths

    # Derived
    final_score: float = 0.0
    policy_denied: bool = False
    reason: str = ""

    # Extra metadata from CodeCompass / RAG
    domain: str = ""
    sensitivity_class: str = ""
    graph_edges: list[dict[str, Any]] = field(default_factory=list)

    def compute_final(self, weights: RankingWeights) -> "ScoredCandidate":
        """Return a *new* candidate with ``final_score`` set."""
        score = (
            self.embedding_score * weights.embedding_score
            + self.symbol_match_score * weights.symbol_match_score
            + self.graph_distance_score * weights.graph_distance_score
            + self.working_file_bonus * weights.working_file_bonus
            + self.domain_scope_bonus * weights.domain_scope_bonus
            + self.test_relation_bonus * weights.test_relation_bonus
            + self.recency_bonus * weights.recency_bonus
            + self.policy_penalty * abs(weights.policy_penalty)
            + self.sensitivity_penalty * abs(weights.sensitivity_penalty)
        )
        return ScoredCandidate(
            path=self.path,
            record_id=self.record_id,
            excerpt=self.excerpt,
            symbols=self.symbols,
            embedding_score=self.embedding_score,
            symbol_match_score=self.symbol_match_score,
            graph_distance_score=self.graph_distance_score,
            working_file_bonus=self.working_file_bonus,
            domain_scope_bonus=self.domain_scope_bonus,
            test_relation_bonus=self.test_relation_bonus,
            recency_bonus=self.recency_bonus,
            policy_penalty=self.policy_penalty,
            sensitivity_penalty=self.sensitivity_penalty,
            final_score=round(max(0.0, min(1.0, score)), 6),
            policy_denied=self.policy_denied,
            reason=self.reason,
            domain=self.domain,
            sensitivity_class=self.sensitivity_class,
            graph_edges=self.graph_edges,
        )


def _sort_key(c: ScoredCandidate) -> tuple[float, str, str]:
    return (-c.final_score, c.path, c.record_id)


class CandidateScorer:
    """Scores and sorts raw CodeCompass / RAG candidates.

    All parameters are injected; no direct dependency on external services.
    """

    def __init__(
        self,
        *,
        weights: RankingWeights | None = None,
        working_files: list[str] | None = None,
        denied_paths: set[str] | None = None,
        sensitive_path_prefixes: list[str] | None = None,
    ) -> None:
        self._weights = weights or RankingWeights()
        self._working_files: frozenset[str] = frozenset(working_files or [])
        self._denied_paths: frozenset[str] = frozenset(denied_paths or [])
        self._sensitive_prefixes: tuple[str, ...] = tuple(sensitive_path_prefixes or [
            "src/security", "src/auth", "src/payment", "src/secrets",
        ])

    def score_all(self, raw_candidates: list[dict[str, Any]]) -> list[ScoredCandidate]:
        """Score and rank a list of raw candidate dicts from CodeCompass/RAG.

        Each raw candidate should contain at minimum: ``path`` and ``record_id``.
        Optional fields: ``embedding_score``, ``symbol_match_score``,
        ``graph_distance``, ``domain``, ``sensitivity_class``, ``graph_edges``,
        ``excerpt``, ``symbols``, ``is_test``, ``commit_recency``.
        """
        scored: list[ScoredCandidate] = []
        for raw in raw_candidates:
            c = self._score_one(raw)
            scored.append(c.compute_final(self._weights))
        scored.sort(key=_sort_key)
        return scored

    def _score_one(self, raw: dict[str, Any]) -> ScoredCandidate:
        path = str(raw.get("path") or "")
        record_id = str(raw.get("record_id") or _stable_id(path))

        emb = _clamp(raw.get("embedding_score") or 0.0)
        sym = _clamp(raw.get("symbol_match_score") or 0.0)

        # Graph distance: convert raw distance to a 0..1 score (closer = higher)
        graph_dist_raw = raw.get("graph_distance")
        if graph_dist_raw is not None:
            try:
                d = float(graph_dist_raw)
                graph_dist_score = max(0.0, 1.0 - d / 5.0)
            except (TypeError, ValueError):
                graph_dist_score = 0.0
        else:
            graph_dist_score = 0.0

        working_bonus = 0.5 if path in self._working_files else 0.0
        domain = str(raw.get("domain") or "")
        domain_bonus = 0.3 if domain else 0.0
        is_test = bool(raw.get("is_test") or "/test" in path or "test_" in path.rsplit("/", 1)[-1])
        test_bonus = 0.2 if is_test else 0.0
        commit_rec = raw.get("commit_recency")
        try:
            recency = _clamp(float(commit_rec)) if commit_rec is not None else 0.0
        except (TypeError, ValueError):
            recency = 0.0

        policy_denied = path in self._denied_paths
        policy_pen = -1.0 if policy_denied else 0.0
        sensitivity_class = str(raw.get("sensitivity_class") or "")
        sens_pen = -0.5 if (sensitivity_class == "high" or any(path.startswith(p) for p in self._sensitive_prefixes)) else 0.0

        reason_parts: list[str] = []
        if working_bonus:
            reason_parts.append("working_file")
        if policy_denied:
            reason_parts.append("policy_denied")
        if sens_pen < 0:
            reason_parts.append("sensitive_path")
        reason = "; ".join(reason_parts)

        return ScoredCandidate(
            path=path,
            record_id=record_id,
            excerpt=str(raw.get("excerpt") or "")[:500],
            symbols=list(raw.get("symbols") or []),
            embedding_score=emb,
            symbol_match_score=sym,
            graph_distance_score=graph_dist_score,
            working_file_bonus=working_bonus,
            domain_scope_bonus=domain_bonus,
            test_relation_bonus=test_bonus,
            recency_bonus=recency,
            policy_penalty=policy_pen,
            sensitivity_penalty=sens_pen,
            policy_denied=policy_denied,
            reason=reason,
            domain=domain,
            sensitivity_class=sensitivity_class,
            graph_edges=list(raw.get("graph_edges") or []),
        )


def _clamp(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _stable_id(path: str) -> str:
    return hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()[:12]
