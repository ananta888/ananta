"""TQ-024 — Hybrid ranking stability regression tests.

These tests verify that switching encoding modes does not silently invert
or shuffle retrieval rankings in ways that hurt retrieval quality.

A MinimalRankingHarness wraps CodeCompassVectorStore + CodeCompassVectorEngine
with a deterministic FakeEmbeddingProvider.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
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
    dimensions: int = 16

    def config_hash(self) -> str:
        return "fakehash-16"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return L2-normalized hash-based deterministic vectors."""
        result = []
        for i, text in enumerate(texts):
            rng = random.Random(hash(text) & 0xFFFFFFFF)
            raw = [rng.gauss(0.0, 1.0) for _ in range(self.dimensions)]
            norm = math.sqrt(sum(x * x for x in raw))
            if norm < 1e-12:
                result.append([0.0] * self.dimensions)
            else:
                result.append([x / norm for x in raw])
        return result


# ---------------------------------------------------------------------------
# Fixture documents with varied embedding texts to create non-trivial ranking
# ---------------------------------------------------------------------------

_RANKING_DOCS: list[dict[str, Any]] = [
    {"record_id": "r01", "kind": "python_function", "file": "src/auth.py",
     "embedding_text": "authenticate user password hash verify bcrypt"},
    {"record_id": "r02", "kind": "python_function", "file": "src/search.py",
     "embedding_text": "vector similarity cosine search top_k results"},
    {"record_id": "r03", "kind": "java_class", "file": "src/Payment.java",
     "embedding_text": "payment gateway stripe webhook charge refund"},
    {"record_id": "r04", "kind": "config", "file": "config/db.yml",
     "embedding_text": "database postgres connection pool credentials"},
    {"record_id": "r05", "kind": "python_module", "file": "src/cache.py",
     "embedding_text": "redis cache ttl eviction key-value store"},
    {"record_id": "r06", "kind": "markdown", "file": "docs/api.md",
     "embedding_text": "rest api openapi swagger endpoint documentation"},
    {"record_id": "r07", "kind": "python_function", "file": "src/email.py",
     "embedding_text": "email smtp send notification template html"},
    {"record_id": "r08", "kind": "java_method", "file": "src/Invoice.java",
     "embedding_text": "invoice tax calculation total vat subtotal"},
]


# ---------------------------------------------------------------------------
# MinimalRankingHarness
# ---------------------------------------------------------------------------

class MinimalRankingHarness:
    """Thin wrapper around store + engine for ranking stability testing."""

    def __init__(
        self,
        *,
        index_path: Path,
        documents: list[dict[str, Any]],
        provider: FakeEmbeddingProvider,
        mode: str = "off",
    ):
        self.provider = provider
        self.store = CodeCompassVectorStore(index_path=index_path)
        self.encoder = VectorEncoder(VectorEncodingProfile(mode=mode))
        self.engine = CodeCompassVectorEngine(
            store=self.store,
            embedding_provider=self.provider,
        )
        self.store.rebuild(
            documents=documents,
            embedding_provider=self.provider,
            retrieval_cache_state="cs-ranking",
            manifest_hash="mh-ranking",
            vector_encoder=self.encoder,
        )

    def search(self, query: str, top_k: int = 5) -> list[str]:
        """Return list of record_ids in ranked order."""
        results = self.engine.search(query=query, top_k=top_k)
        return [str(r.get("record_id") or r.get("metadata", {}).get("record_id") or "") for r in results]


# ---------------------------------------------------------------------------
# TQ-024-001  ranking is stable across two rebuilds with same config
# ---------------------------------------------------------------------------

def test_ranking_stable_across_rebuilds(tmp_path):
    """Two consecutive rebuilds with the same config must produce the same ranking."""
    provider = FakeEmbeddingProvider()
    h1 = MinimalRankingHarness(
        index_path=tmp_path / "idx1.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="off",
    )
    h2 = MinimalRankingHarness(
        index_path=tmp_path / "idx2.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="off",
    )
    query = "vector similarity search"
    order1 = h1.search(query, top_k=5)
    order2 = h2.search(query, top_k=5)
    assert order1 == order2, f"Ranking not stable across rebuilds:\n  1st: {order1}\n  2nd: {order2}"


# ---------------------------------------------------------------------------
# TQ-024-002  mode=float32 and mode=off produce the same top-3 order
# ---------------------------------------------------------------------------

def test_ranking_stable_float32_vs_off(tmp_path):
    """float32 and off are both lossless; their top-3 ranking must be identical."""
    provider = FakeEmbeddingProvider()
    h_off = MinimalRankingHarness(
        index_path=tmp_path / "idx_off.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="off",
    )
    h_f32 = MinimalRankingHarness(
        index_path=tmp_path / "idx_f32.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="float32",
    )
    query = "payment webhook stripe"
    top3_off = h_off.search(query, top_k=3)
    top3_f32 = h_f32.search(query, top_k=3)
    assert top3_off == top3_f32, (
        f"mode=off and mode=float32 produce different top-3 rankings:\n"
        f"  off: {top3_off}\n  float32: {top3_f32}"
    )


# ---------------------------------------------------------------------------
# TQ-024-003  int8 encoding does not invert the top result vs float32
# ---------------------------------------------------------------------------

def test_int8_encoding_does_not_invert_top_result(tmp_path):
    """For a clear semantic winner, int8 must agree with float32 on top-1."""
    provider = FakeEmbeddingProvider()
    h_f32 = MinimalRankingHarness(
        index_path=tmp_path / "idx_f32.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="float32",
    )
    h_int8 = MinimalRankingHarness(
        index_path=tmp_path / "idx_int8.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="int8",
    )
    # Use a query that closely matches one specific document to create a clear winner
    query = "authenticate user password bcrypt verify hash"
    top1_f32 = h_f32.search(query, top_k=5)[0]
    top1_int8 = h_int8.search(query, top_k=5)[0]
    assert top1_f32 == top1_int8, (
        f"int8 encoding inverted the top result:\n"
        f"  float32 top-1: {top1_f32}\n  int8 top-1: {top1_int8}"
    )


# ---------------------------------------------------------------------------
# TQ-024-004  float16 top-3 matches float32
# ---------------------------------------------------------------------------

def test_float16_ranking_matches_float32(tmp_path):
    """float16 is high-precision; its top-3 ranking must match float32."""
    provider = FakeEmbeddingProvider()
    h_f32 = MinimalRankingHarness(
        index_path=tmp_path / "idx_f32.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="float32",
    )
    h_f16 = MinimalRankingHarness(
        index_path=tmp_path / "idx_f16.json",
        documents=_RANKING_DOCS,
        provider=provider,
        mode="float16",
    )
    query = "database postgres connection pool"
    top3_f32 = h_f32.search(query, top_k=3)
    top3_f16 = h_f16.search(query, top_k=3)
    assert top3_f32 == top3_f16, (
        f"float16 top-3 diverges from float32:\n"
        f"  float32: {top3_f32}\n  float16: {top3_f16}"
    )


# ---------------------------------------------------------------------------
# TQ-024-005  switching mode=off→int8 changes config hash and triggers refresh
# ---------------------------------------------------------------------------

def test_encoding_change_triggers_rebuild(tmp_path):
    """Switching mode=off to mode=int8 must produce a different config_hash and trigger refresh."""
    profile_off = VectorEncodingProfile(mode="off")
    profile_int8 = VectorEncodingProfile(mode="int8")
    assert profile_off.config_hash() != profile_int8.config_hash(), (
        "mode=off and mode=int8 must not share the same config_hash"
    )

    provider = FakeEmbeddingProvider()
    store = CodeCompassVectorStore(index_path=tmp_path / "idx_switch.json")
    docs = _RANKING_DOCS[:3]

    store.rebuild(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(profile_off),
    )
    result = store.refresh(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(profile_int8),
    )
    assert result["reason"] == "vector_encoding_changed", (
        f"Expected reason='vector_encoding_changed', got {result['reason']!r}"
    )
    assert result["mode"] == "rebuild"
    # After switch the stored mode must be int8
    loaded_state = store.load()["state"]
    assert loaded_state["vector_encoding_profile"]["mode"] == "int8"
