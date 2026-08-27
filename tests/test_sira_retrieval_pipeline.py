from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from worker.retrieval.sira.config import SiraConfig, SiraMode
from worker.retrieval.sira.contracts import CorpusBinding
from worker.retrieval.sira.enriched_fts_store import EnrichedFtsStore
from worker.retrieval.sira.pointwise_reranker import PointwiseReranker
from worker.retrieval.sira.query_expander import QueryExpander
from worker.retrieval.sira.router import SiraCircuitBreaker, SiraRouter
from worker.retrieval.sira.service import SiraRetrievalError, SiraRetrievalService
from worker.retrieval.sira.term_validator import CorpusTermValidator
from worker.retrieval.sira.weighted_lexical_retriever import WeightedLexicalRetriever
from worker.retrieval.sira.weighted_query_compiler import WeightedQueryCompiler


class FakeGenerator:
    model_id = "local-query"
    model_digest = "sha256:local-query"
    local = True

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "evidence_sketch": "Find retry policy for failed payments",
            "terms": [
                {"value": "backoff", "confidence": 0.95},
                {"value": "nonexistent", "confidence": 0.9},
            ],
        }


class FakeScorer:
    model_digest = "sha256:reranker"

    def score(self, *, query: str, candidate: Mapping[str, Any]) -> float:
        return 1.0 if candidate["record_id"] == "r1" and "payment" in query else 0.0


def _binding() -> CorpusBinding:
    return CorpusBinding(
        tenant_id="tenant-a",
        scope="repo-a",
        repository_revision="rev-1",
        source_manifest_hash="manifest-1",
        index_digest="index-1",
        statistics_digest="stats-1",
        profile_version="corpus-discriminative-lexical.v1",
    )


def _documents() -> list[dict[str, Any]]:
    return [
        {
            "record_id": "r1",
            "kind": "python_function",
            "file": "src/payment.py",
            "document_hash": "d1",
            "source_id": "",
            "source_version": "rev-1",
            "tenant_id": "tenant-a",
            "scope": "repo-a",
            "text_fields": {
                "symbol_text": "retryPayment",
                "path_text": "src/payment.py",
                "summary_text": "retry a failed payment",
                "content_text": "exponential retry policy",
                "relation_text": "calls gateway",
            },
        },
        {
            "record_id": "r2",
            "kind": "python_function",
            "file": "src/report.py",
            "document_hash": "d2",
            "source_id": "",
            "source_version": "rev-1",
            "tenant_id": "tenant-a",
            "scope": "repo-a",
            "text_fields": {
                "symbol_text": "renderReport",
                "path_text": "src/report.py",
                "summary_text": "render accounting report",
                "content_text": "format table rows",
                "relation_text": "",
            },
        },
    ]


def _build(tmp_path: Path, *, mode: SiraMode = SiraMode.PREFERRED) -> SiraRetrievalService:
    binding = _binding()
    store = EnrichedFtsStore(db_path=tmp_path / "sira.sqlite")
    store.rebuild(
        documents=_documents(),
        enrichments={
            "r1": {"generated_terms": [{"value": "backoff"}, {"value": "resilience"}]},
            "r2": {"generated_terms": []},
        },
        binding=binding,
    )
    config = SiraConfig(mode=mode, query_model="local-query")
    return SiraRetrievalService(
        config=config,
        binding=binding,
        router=SiraRouter(config=config),
        expander=QueryExpander(config=config, generator=FakeGenerator()),
        validator=CorpusTermValidator(statistics=store, config=config),
        compiler=WeightedQueryCompiler(),
        retriever=WeightedLexicalRetriever(store=store),
        baseline_search=lambda query, top_k: [
            {"record_id": "baseline", "path": "baseline.py", "score": 1.0, "content": query}
        ][:top_k],
    )


def test_weighted_pipeline_executes_one_lexical_call_and_preserves_channel(tmp_path: Path):
    result = _build(tmp_path).retrieve(query="payment failure handling", top_k=4, corpus_ready=True)
    assert result["trace"]["lexical_retrieval_calls"] == 1
    assert result["trace"]["compiled_query"]["original_query"] == "payment failure handling"
    assert result["trace"]["term_decisions"][0]["reason_code"] == "accepted"
    assert result["trace"]["term_decisions"][1]["reason_code"] == "term_absent_from_scope"
    assert result["selected_candidates"][0]["record_id"] == "r1"
    assert result["selected_candidates"][0]["channel"] == "codecompass_fts"
    assert result["selected_candidates"][0]["metadata"]["retrieval_profile"] == "corpus_discriminative_lexical"


def test_shadow_does_not_change_selected_results(tmp_path: Path):
    result = _build(tmp_path, mode=SiraMode.SHADOW).retrieve(
        query="payment failure handling", top_k=4, corpus_ready=True
    )
    assert result["selected_candidates"][0]["record_id"] == "baseline"
    assert result["shadow_candidates"][0]["record_id"] == "r1"
    assert result["trace"]["shadow_non_effecting"] is True
    assert result["trace"]["lexical_retrieval_calls"] == 2


def test_preferred_falls_back_and_required_fails_typed(tmp_path: Path):
    preferred = _build(tmp_path, mode=SiraMode.PREFERRED).retrieve(
        query="payment failure handling", top_k=2, corpus_ready=False
    )
    assert preferred["selected_candidates"][0]["record_id"] == "baseline"
    assert preferred["trace"]["fallback_reason"] == "corpus_unavailable"

    with pytest.raises(SiraRetrievalError, match="corpus_unavailable"):
        _build(tmp_path / "required", mode=SiraMode.REQUIRED).retrieve(
            query="payment failure handling", top_k=2, corpus_ready=False
        )


def test_circuit_breaker_recovers_after_cooldown():
    breaker = SiraCircuitBreaker(failure_threshold=2, recovery_seconds=10)
    breaker.record_failure(now=100)
    breaker.record_failure(now=101)
    assert breaker.allow(now=105) is False
    assert breaker.allow(now=112) is True


def test_pointwise_reranker_keeps_lexical_evidence_separate():
    candidates = [{"record_id": "r1", "path": "a.py", "content": "payment retry", "score": 2.0}]
    rows, trace = PointwiseReranker(scorer=FakeScorer(), timeout_ms=1_000).rerank(
        "payment failure",
        candidates,
        top_n=1,
    )
    assert rows[0]["score"] == 2.2
    assert rows[0]["metadata"]["sira_pointwise_score"] == 1.0
    assert trace["model_digest"] == "sha256:reranker"
    assert candidates[0]["score"] == 2.0


def test_store_fails_closed_on_statistics_binding_mismatch(tmp_path: Path):
    binding = _binding()
    store = EnrichedFtsStore(db_path=tmp_path / "sira.sqlite")
    store.rebuild(documents=_documents(), enrichments={}, binding=binding)
    stale = CorpusBinding(
        tenant_id=binding.tenant_id,
        scope=binding.scope,
        repository_revision=binding.repository_revision,
        source_manifest_hash=binding.source_manifest_hash,
        index_digest=binding.index_digest,
        statistics_digest="stale-stats",
        profile_version=binding.profile_version,
    )
    with pytest.raises(ValueError, match="sira_statistics_mismatch"):
        store.lookup(["payment"], binding=stale)
