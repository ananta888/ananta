from __future__ import annotations

from agent.hybrid_orchestrator import ContextChunk, ContextManager


def test_exact_repository_map_signal_beats_fuzzy_vector_signal() -> None:
    manager = ContextManager()
    chunks = [
        ContextChunk(
            engine="codecompass_vector",
            source="src/semantic.py",
            content="payment processing related behaviour",
            score=1.0,
        ),
        ContextChunk(
            engine="repository_map",
            source="src/payment_service.py",
            content="class PaymentService:\n    def retry_timeout(self): pass",
            score=0.7,
        ),
    ]

    ranked = manager.rerank(
        chunks=chunks,
        query="PaymentService retry_timeout",
        max_chunks=2,
        max_chars=4000,
        max_tokens=1000,
    )

    assert ranked[0].engine == "repository_map"
    assert ranked[0].source == "src/payment_service.py"


def test_vector_signal_is_kept_when_repository_map_has_no_match() -> None:
    manager = ContextManager()
    chunks = [
        ContextChunk(
            engine="codecompass_vector",
            source="src/vector_only.py",
            content="semantic retry policy details",
            score=0.9,
        )
    ]

    ranked = manager.rerank(
        chunks=chunks,
        query="retry policy",
        max_chunks=2,
        max_chars=4000,
        max_tokens=1000,
    )

    assert ranked[0].engine == "codecompass_vector"


def test_same_source_cross_engine_signals_are_deduplicated() -> None:
    manager = ContextManager()
    chunks = [
        ContextChunk(
            engine="repository_map",
            source="src/payment.py",
            content="class PaymentService: pass",
            score=0.8,
        ),
        ContextChunk(
            engine="codecompass_vector",
            source="src/payment.py",
            content="payment retry vector description",
            score=0.9,
        ),
    ]

    ranked = manager.rerank(
        chunks=chunks,
        query="payment",
        max_chunks=4,
        max_chars=4000,
        max_tokens=1000,
    )

    assert len([chunk for chunk in ranked if chunk.source == "src/payment.py"]) == 1
    assert set(ranked[0].metadata["cross_engine_signals"].split(",")) == {
        "codecompass_vector",
        "repository_map",
    }
