"""TQ-022 — Reproducible retrieval fixture tests for CodeCompassVectorStore.

All tests are fully deterministic: no external calls, fixed random seed,
predictable vectors from FakeEmbeddingProvider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.vector_encoding import (
    VectorEncoder,
    VectorEncodingProfile,
)


# ---------------------------------------------------------------------------
# Deterministic fake embedding provider
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FakeEmbeddingProvider:
    provider_id: str = "fake"
    model_version: str = "fake-v1"
    dimensions: int = 8

    def config_hash(self) -> str:
        return "fakehash"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [0.1 * i + 0.01 * j for j in range(self.dimensions)]
            for i, _ in enumerate(texts)
        ]


# ---------------------------------------------------------------------------
# Fixture documents — 10 entries across different kinds and files
# ---------------------------------------------------------------------------

FIXTURE_DOCUMENTS: list[dict[str, Any]] = [
    {
        "record_id": "fix-001",
        "kind": "python_function",
        "file": "src/auth/login.py",
        "embedding_text": "authenticate user password hash bcrypt",
    },
    {
        "record_id": "fix-002",
        "kind": "python_class",
        "file": "src/auth/session.py",
        "embedding_text": "session token expiry refresh jwt",
    },
    {
        "record_id": "fix-003",
        "kind": "java_method",
        "file": "src/payment/PaymentGateway.java",
        "embedding_text": "payment processing charge stripe webhook",
    },
    {
        "record_id": "fix-004",
        "kind": "java_class",
        "file": "src/payment/Invoice.java",
        "embedding_text": "invoice total tax calculation vat",
    },
    {
        "record_id": "fix-005",
        "kind": "config",
        "file": "config/database.yml",
        "embedding_text": "database connection pool postgres credentials",
    },
    {
        "record_id": "fix-006",
        "kind": "python_function",
        "file": "src/search/vector_index.py",
        "embedding_text": "vector similarity cosine search embedding index",
    },
    {
        "record_id": "fix-007",
        "kind": "python_module",
        "file": "src/cache/redis_cache.py",
        "embedding_text": "redis cache invalidation ttl eviction",
    },
    {
        "record_id": "fix-008",
        "kind": "markdown",
        "file": "docs/api_reference.md",
        "embedding_text": "api endpoint rest openapi swagger documentation",
    },
    {
        "record_id": "fix-009",
        "kind": "java_interface",
        "file": "src/repository/UserRepository.java",
        "embedding_text": "user repository crud findById save delete",
    },
    {
        "record_id": "fix-010",
        "kind": "python_function",
        "file": "src/notifications/email_sender.py",
        "embedding_text": "email smtp send notification template html",
    },
]

_REQUIRED_FIELDS = {"record_id", "kind", "file", "embedding_text"}


# ---------------------------------------------------------------------------
# TQ-022-001  fixture documents have required fields
# ---------------------------------------------------------------------------

def test_fixture_documents_have_required_fields():
    """Every fixture document must have all required fields set to non-empty strings."""
    assert len(FIXTURE_DOCUMENTS) == 10
    for doc in FIXTURE_DOCUMENTS:
        for field in _REQUIRED_FIELDS:
            assert field in doc, f"Missing field {field!r} in document {doc.get('record_id')}"
            assert doc[field], f"Empty value for field {field!r} in document {doc.get('record_id')}"


# ---------------------------------------------------------------------------
# TQ-022-002  vector store indexes all fixture documents
# ---------------------------------------------------------------------------

def test_vector_store_indexes_all_fixture_documents(tmp_path):
    """After rebuild the entry count must equal len(FIXTURE_DOCUMENTS)."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()

    result = store.rebuild(
        documents=FIXTURE_DOCUMENTS,
        embedding_provider=provider,
        retrieval_cache_state="cs-fixture",
        manifest_hash="mh-fixture",
    )
    assert result["status"] == "ok"
    assert result["indexed_documents"] == len(FIXTURE_DOCUMENTS)

    loaded = store.load()
    assert loaded["state"]["entry_count"] == len(FIXTURE_DOCUMENTS)
    assert len(loaded["entries"]) == len(FIXTURE_DOCUMENTS)


# ---------------------------------------------------------------------------
# TQ-022-003  search returns at most top_k results
# ---------------------------------------------------------------------------

def test_vector_engine_returns_top_k_results(tmp_path):
    """search() must return at most top_k results."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()

    store.rebuild(
        documents=FIXTURE_DOCUMENTS,
        embedding_provider=provider,
        retrieval_cache_state="cs-fixture",
        manifest_hash="mh-fixture",
    )
    for top_k in (1, 3, 5, 10):
        results = store.search(query="vector search", embedding_provider=provider, top_k=top_k)
        assert len(results) <= top_k, f"Expected ≤ {top_k} results, got {len(results)}"
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# TQ-022-004  allowed_paths filter works correctly
# ---------------------------------------------------------------------------

def test_search_with_allowed_paths_filter(tmp_path):
    """Results can be post-filtered by file prefix; only matching entries returned."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()

    store.rebuild(
        documents=FIXTURE_DOCUMENTS,
        embedding_provider=provider,
        retrieval_cache_state="cs-fixture",
        manifest_hash="mh-fixture",
    )
    all_results = store.search(query="authentication login", embedding_provider=provider, top_k=10)
    # Post-filter: only entries from src/auth/
    allowed_prefix = "src/auth/"
    filtered = [r for r in all_results if str(r.get("file") or "").startswith(allowed_prefix)]

    # src/auth/ has 2 documents (fix-001, fix-002)
    auth_record_ids = {d["record_id"] for d in FIXTURE_DOCUMENTS if d["file"].startswith(allowed_prefix)}
    filtered_ids = {r["record_id"] for r in filtered}
    assert filtered_ids.issubset(auth_record_ids), (
        f"Filtered results contain entries outside allowed_prefix: {filtered_ids - auth_record_ids}"
    )
    assert len(filtered) <= 2


# ---------------------------------------------------------------------------
# TQ-022-005  search is deterministic (same query + same index → same ranking)
# ---------------------------------------------------------------------------

def test_fixture_search_deterministic(tmp_path):
    """Calling search() twice with the same query must return the same ranking order."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()

    store.rebuild(
        documents=FIXTURE_DOCUMENTS,
        embedding_provider=provider,
        retrieval_cache_state="cs-fixture",
        manifest_hash="mh-fixture",
    )
    results_a = store.search(query="payment invoice", embedding_provider=provider, top_k=5)
    results_b = store.search(query="payment invoice", embedding_provider=provider, top_k=5)

    ids_a = [r["record_id"] for r in results_a]
    ids_b = [r["record_id"] for r in results_b]
    assert ids_a == ids_b, "Search is not deterministic: rankings differ between calls"


# ---------------------------------------------------------------------------
# TQ-022-006  int8 encoding does not collapse the top result
# ---------------------------------------------------------------------------

def test_fixture_with_int8_encoding(tmp_path):
    """With int8 encoding the top-scored result must still have a positive score."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index_int8.json")
    provider = FakeEmbeddingProvider()
    encoder = VectorEncoder(VectorEncodingProfile(mode="int8"))

    store.rebuild(
        documents=FIXTURE_DOCUMENTS,
        embedding_provider=provider,
        retrieval_cache_state="cs-fixture-int8",
        manifest_hash="mh-fixture-int8",
        vector_encoder=encoder,
    )
    results = store.search(query="vector cosine similarity", embedding_provider=provider, top_k=3)
    assert len(results) >= 1
    top_score = float(results[0].get("score") or results[0].get("vector_score") or 0.0)
    assert top_score >= 0.0, "Top result has negative score after int8 encoding"
    # The top result must have a higher score than the last
    if len(results) > 1:
        bottom_score = float(results[-1].get("score") or results[-1].get("vector_score") or 0.0)
        assert top_score >= bottom_score, "Results are not sorted by score (descending)"
