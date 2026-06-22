"""CodeCompass retrieval strategy implementations.

Strategies control HOW CodeCompass vector candidates are selected and filtered
before they enter the main context pipeline. The key problem they solve:
deterministic graph/vector retrieval does not understand natural language intent
like "I'm interested in X, not Y" — the transformer-based strategies close this gap.

Strategy reference
──────────────────
STRATEGY_DIRECT
    Pass-through. No transformer involvement. Current default behaviour.
    Fastest, cheapest. Use when query is already precise enough.

STRATEGY_SEMANTIC_PREFILTER
    CrossEncoder (RestrictedModelInferenceService.rerank) applied to the
    CodeCompass candidates BEFORE they merge with other retrieval sources.
    Fetches top_k × multiplier candidates, reranks with full attention
    (understands negation / contrast), then keeps only those above the
    configurable score threshold.
    Recommended for queries with explicit inclusion/exclusion intent.

STRATEGY_TRANSFORMER_RERANK
    Relies on the existing post-merge restricted reranking path
    (pre_model_context_orchestrator._maybe_restricted_rerank). This strategy
    signals the orchestrator to enable that path; no extra work happens here
    at the CodeCompass vector level itself.
    Good for general quality improvement without the overhead of two rerank passes.

STRATEGY_HYBRID
    Combines SEMANTIC_PREFILTER (early, CC-level) with TRANSFORMER_RERANK
    (late, after all sources merged). Best signal quality; highest latency.

STRATEGY_LLM_DISPATCHER  [stub]
    Full LLM call extracts structured intent before CC is queried.
    Falls back to DIRECT until implemented.

STRATEGY_LLM_TOOL  [stub]
    CodeCompass exposed as a tool the LLM calls in an iterative loop.
    Architectural change; falls back to DIRECT.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.services.restricted_model_inference_service import RestrictedModelInferenceService

log = logging.getLogger(__name__)

# ── Strategy constants ────────────────────────────────────────────────────────

STRATEGY_DIRECT             = "direct"
STRATEGY_SEMANTIC_PREFILTER = "semantic_prefilter"
STRATEGY_TRANSFORMER_RERANK = "transformer_rerank"
STRATEGY_HYBRID             = "hybrid"
STRATEGY_LLM_DISPATCHER     = "llm_dispatcher"
STRATEGY_LLM_TOOL           = "llm_tool"

ALL_STRATEGIES = frozenset({
    STRATEGY_DIRECT,
    STRATEGY_SEMANTIC_PREFILTER,
    STRATEGY_TRANSFORMER_RERANK,
    STRATEGY_HYBRID,
    STRATEGY_LLM_DISPATCHER,
    STRATEGY_LLM_TOOL,
})

# Strategies that apply a pre-filter at the CodeCompass vector level.
PREFILTER_STRATEGIES = frozenset({STRATEGY_SEMANTIC_PREFILTER, STRATEGY_HYBRID})

# Strategies that signal the post-merge orchestrator to enable transformer reranking.
POSTRANK_STRATEGIES = frozenset({STRATEGY_TRANSFORMER_RERANK, STRATEGY_HYBRID})


# ── Strategy config ───────────────────────────────────────────────────────────

class RetrievalStrategyConfig:
    """Thin config container parsed from the codecompass_ranking config dict."""

    __slots__ = (
        "strategy",
        "semantic_prefilter_threshold",
        "semantic_prefilter_top_k_multiplier",
        "semantic_prefilter_min_results",
    )

    def __init__(
        self,
        *,
        strategy: str = STRATEGY_DIRECT,
        semantic_prefilter_threshold: float = 0.25,
        semantic_prefilter_top_k_multiplier: int = 2,
        semantic_prefilter_min_results: int = 1,
    ) -> None:
        self.strategy = strategy if strategy in ALL_STRATEGIES else STRATEGY_DIRECT
        self.semantic_prefilter_threshold = float(semantic_prefilter_threshold or 0.25)
        self.semantic_prefilter_top_k_multiplier = max(1, int(semantic_prefilter_top_k_multiplier or 2))
        self.semantic_prefilter_min_results = max(0, int(semantic_prefilter_min_results or 1))

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "RetrievalStrategyConfig":
        d = dict(raw or {})
        try:
            threshold = float(d.get("semantic_prefilter_threshold") or 0.25)
        except (TypeError, ValueError):
            threshold = 0.25
        try:
            multiplier = int(d.get("semantic_prefilter_top_k_multiplier") or 2)
        except (TypeError, ValueError):
            multiplier = 2
        try:
            min_results = int(d.get("semantic_prefilter_min_results") or 1)
        except (TypeError, ValueError):
            min_results = 1
        return cls(
            strategy=str(d.get("retrieval_strategy") or STRATEGY_DIRECT),
            semantic_prefilter_threshold=threshold,
            semantic_prefilter_top_k_multiplier=multiplier,
            semantic_prefilter_min_results=min_results,
        )

    def wants_prefilter(self) -> bool:
        return self.strategy in PREFILTER_STRATEGIES

    def wants_postrank(self) -> bool:
        return self.strategy in POSTRANK_STRATEGIES

    def effective_top_k(self, requested_top_k: int) -> int:
        """Top-k to pass to the vector engine (inflated for prefilter strategies)."""
        if self.wants_prefilter():
            return max(1, requested_top_k * self.semantic_prefilter_top_k_multiplier)
        return max(1, requested_top_k)

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieval_strategy": self.strategy,
            "semantic_prefilter_threshold": self.semantic_prefilter_threshold,
            "semantic_prefilter_top_k_multiplier": self.semantic_prefilter_top_k_multiplier,
            "semantic_prefilter_min_results": self.semantic_prefilter_min_results,
        }


# ── Pre-filter application ────────────────────────────────────────────────────

def apply_semantic_prefilter(
    rows: list[dict[str, Any]],
    query: str,
    *,
    restricted_inference: "RestrictedModelInferenceService",
    config: RetrievalStrategyConfig,
    requested_top_k: int,
) -> list[dict[str, Any]]:
    """Rerank CodeCompass vector rows with a CrossEncoder, then threshold-filter.

    Falls back gracefully to returning the original (already score-sorted) rows
    if the transformer service is unavailable or raises.
    """
    if not rows:
        return rows

    candidates = _rows_to_rerank_candidates(rows)
    try:
        rerank_results = restricted_inference.rerank(query, candidates)
    except Exception as exc:
        log.debug("semantic_prefilter: rerank failed, using original order — %s", exc)
        return rows[:requested_top_k]

    # Map rerank scores back to rows by record_id
    score_map: dict[str, float] = {r.record_id: r.score for r in rerank_results}
    annotated: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        rid = _row_record_id(row)
        rerank_score = score_map.get(rid, 0.0)
        annotated.append((rerank_score, {**row, "_rerank_score": rerank_score}))

    annotated.sort(key=lambda t: -t[0])

    threshold = config.semantic_prefilter_threshold
    filtered = [row for score, row in annotated if score >= threshold]

    # Guarantee minimum results even if below threshold (avoid total blackout)
    if not filtered and config.semantic_prefilter_min_results > 0:
        filtered = [row for _, row in annotated[:config.semantic_prefilter_min_results]]
        log.debug(
            "semantic_prefilter: all %d candidates below threshold %.2f — keeping top %d",
            len(rows), threshold, len(filtered),
        )
    else:
        log.debug(
            "semantic_prefilter: %d/%d candidates passed threshold %.2f",
            len(filtered), len(rows), threshold,
        )

    return filtered[:requested_top_k]


def _rows_to_rerank_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        source = str(row.get("source") or row.get("file") or "")
        content = str(row.get("content") or "")
        candidates.append({
            "path": source,
            "record_id": _row_record_id(row),
            "excerpt": content[:500],
        })
    return candidates


def _row_record_id(row: dict[str, Any]) -> str:
    rid = str(row.get("record_id") or row.get("id") or "")
    if rid:
        return rid
    source = str(row.get("source") or row.get("file") or "")
    return hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()[:12]
