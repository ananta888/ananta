from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from worker.retrieval.sira.config import SiraConfig, SiraMode
from worker.retrieval.sira.contracts import CorpusBinding, PointwiseRerankerPort
from worker.retrieval.sira.hybrid_adapter import SiraHybridAdapter
from worker.retrieval.sira.query_expander import QueryExpander
from worker.retrieval.sira.router import SiraRouter
from worker.retrieval.sira.term_validator import CorpusTermValidator
from worker.retrieval.sira.weighted_lexical_retriever import WeightedLexicalRetriever
from worker.retrieval.sira.weighted_query_compiler import WeightedQueryCompiler, tokenize_original_query


class SiraRetrievalError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class SiraRetrievalService:
    """Execute one Hub-selected SIRA profile request inside one Worker."""

    def __init__(
        self,
        *,
        config: SiraConfig,
        binding: CorpusBinding,
        router: SiraRouter,
        expander: QueryExpander,
        validator: CorpusTermValidator,
        compiler: WeightedQueryCompiler,
        retriever: WeightedLexicalRetriever,
        baseline_search: Callable[[str, int], Sequence[Mapping[str, Any]]],
        reranker: PointwiseRerankerPort | None = None,
    ) -> None:
        self._config = config
        self._binding = binding
        self._router = router
        self._expander = expander
        self._validator = validator
        self._compiler = compiler
        self._retriever = retriever
        self._baseline_search = baseline_search
        self._reranker = reranker

    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        corpus_ready: bool,
        rollout_mode: SiraMode | None = None,
        baseline_margin: float | None = None,
        expansion_cached: bool = False,
        model_budget_available: bool = True,
    ) -> dict[str, Any]:
        decision = self._router.decide(
            query=query,
            corpus_ready=corpus_ready,
            mode=rollout_mode,
            baseline_margin=baseline_margin,
            expansion_cached=expansion_cached,
            model_budget_available=model_budget_available,
        )
        if not decision.execute_sira:
            if decision.required:
                raise SiraRetrievalError(decision.reason_code)
            baseline = [dict(item) for item in self._baseline_search(query, top_k)]
            return self._result(
                query=query,
                candidates=baseline,
                decision=decision.to_dict(),
                fallback_reason=decision.reason_code,
                lexical_calls=1,
            )

        lexical_calls = 0
        try:
            expansion = self._expander.expand(query, binding=self._binding)
            original_terms = tokenize_original_query(query)
            decisions = self._validator.validate(
                expansion.proposed_terms,
                binding=self._binding,
                original_terms=original_terms,
            )
            compiled = self._compiler.compile(
                original_query=query,
                decisions=decisions,
                binding=self._binding,
            )
            lexical_calls = 1
            candidates = [dict(item) for item in self._retriever.retrieve(compiled, top_k=top_k)]
            rerank_trace: Mapping[str, Any] = {"status": "skipped", "reason": "reranker_disabled"}
            if self._config.reranker_enabled and self._reranker is not None:
                reranked_candidates, rerank_trace = self._reranker.rerank(
                    query,
                    candidates,
                    top_n=self._config.rerank_top_n,
                )
                candidates = [dict(item) for item in reranked_candidates]
            adapted = SiraHybridAdapter.adapt(candidates)
            self._router.circuit_breaker.record_success()
            trace = {
                "schema": "codecompass.sira-trace.v1",
                "binding": self._binding.to_dict(),
                "config": self._config.safe_dict(),
                "routing": decision.to_dict(),
                "expansion": expansion.to_dict(),
                "term_decisions": [item.to_dict() for item in decisions],
                "compiled_query": compiled.to_dict(),
                "reranker": dict(rerank_trace),
                "fallback_reason": "",
                "lexical_retrieval_calls": 1,
            }
            if decision.shadow:
                baseline = [dict(item) for item in self._baseline_search(query, top_k)]
                return {
                    "schema": "codecompass.sira-selection.v1",
                    "profile": "corpus_discriminative_lexical",
                    "mode": str(decision.features.get("mode") or self._config.mode.value),
                    "selected_candidates": baseline,
                    "shadow_candidates": adapted,
                    "trace": {**trace, "lexical_retrieval_calls": 2, "shadow_non_effecting": True},
                }
            return {
                "schema": "codecompass.sira-selection.v1",
                "profile": "corpus_discriminative_lexical",
                "mode": str(decision.features.get("mode") or self._config.mode.value),
                "selected_candidates": adapted,
                "shadow_candidates": [],
                "trace": trace,
            }
        except Exception as exc:
            self._router.circuit_breaker.record_failure()
            reason = self._reason(exc)
            if decision.required:
                raise SiraRetrievalError(reason) from exc
            baseline = [dict(item) for item in self._baseline_search(query, top_k)]
            return self._result(
                query=query,
                candidates=baseline,
                decision=decision.to_dict(),
                fallback_reason=reason,
                lexical_calls=lexical_calls + 1,
            )

    def _result(
        self,
        *,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        decision: Mapping[str, Any],
        fallback_reason: str,
        lexical_calls: int,
    ) -> dict[str, Any]:
        return {
            "schema": "codecompass.sira-selection.v1",
            "profile": "corpus_discriminative_lexical",
            "mode": str((decision.get("features") or {}).get("mode") or self._config.mode.value),
            "selected_candidates": [dict(item) for item in candidates],
            "shadow_candidates": [],
            "trace": {
                "schema": "codecompass.sira-trace.v1",
                "binding": self._binding.to_dict(),
                "config": self._config.safe_dict(),
                "routing": dict(decision),
                "fallback_reason": fallback_reason,
                "lexical_retrieval_calls": lexical_calls,
                "query_original": query,
            },
        }

    @staticmethod
    def _reason(error: Exception) -> str:
        if isinstance(error, ValueError) and str(error).startswith("sira_"):
            return str(error)
        if isinstance(error, SiraRetrievalError):
            return error.reason_code
        return "sira_execution_failed"
