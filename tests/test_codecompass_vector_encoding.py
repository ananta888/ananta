from __future__ import annotations

import pytest

from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.vector_encoding import (
    VectorEncoder,
    VectorEncodingError,
    VectorEncodingProfile,
)


def test_vector_encoding_profile_hash_changes_with_mode():
    off = VectorEncodingProfile(mode="off")
    int8 = VectorEncodingProfile(mode="int8", target_bits=8)

    assert off.config_hash() != int8.config_hash()
    assert not off.enabled
    assert int8.enabled


def test_vector_encoding_rejects_invalid_mode():
    with pytest.raises(VectorEncodingError):
        VectorEncodingProfile(mode="made_up")


def test_int8_roundtrip_is_deterministic():
    encoder = VectorEncoder(VectorEncodingProfile(mode="int8", target_bits=8))
    vector = [0.0, 0.5, -0.25, 1.0, -1.0]

    encoded_a = encoder.encode(vector)
    encoded_b = encoder.encode(vector)
    decoded = encoder.decode(encoded_a)

    assert encoded_a.as_dict() == encoded_b.as_dict()
    assert encoded_a.mode == "int8"
    assert encoded_a.diagnostics["compression_ratio_vs_float32"] > 1.0
    assert len(decoded) == len(vector)
    assert max(abs(a - b) for a, b in zip(vector, decoded, strict=False)) < 0.02


def test_symmetric4bit_roundtrip_marks_experimental():
    encoder = VectorEncoder(VectorEncodingProfile(mode="symmetric4bit", target_bits=4))
    vector = [0.0, 0.5, -0.25, 1.0, -1.0]

    encoded = encoder.encode(vector)
    decoded = encoder.decode(encoded)

    assert encoded.mode == "symmetric4bit"
    assert encoded.diagnostics["experimental"] is True
    assert "experimental_warning" in encoded.diagnostics
    assert len(decoded) == len(vector)


def test_turboquant_mse_experimental_is_visible_and_deterministic():
    encoder = VectorEncoder(VectorEncodingProfile(mode="turboquant_mse_experimental", target_bits=4, seed=123))
    vector = [0.1, -0.2, 0.3, -0.4, 0.5]

    encoded_a = encoder.encode(vector)
    encoded_b = encoder.encode(vector)

    assert encoded_a.as_dict() == encoded_b.as_dict()
    assert encoded_a.mode == "turboquant_mse_experimental"
    assert encoded_a.diagnostics["experimental"] is True
    assert "TurboQuant-inspired" in encoded_a.diagnostics["experimental_warning"]


def test_vector_store_rebuild_with_int8_encoding(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    provider = FakeEmbeddingProvider(model_version="fake-v2", dimensions=5)
    encoder = VectorEncoder(VectorEncodingProfile(mode="int8", target_bits=8))
    documents = [
        {
            "record_id": "r1",
            "kind": "java_method",
            "file": "src/PaymentService.java",
            "manifest_hash": "mh-1",
            "embedding_text": "payment retry timeout method",
        },
        {
            "record_id": "r2",
            "kind": "java_method",
            "file": "src/InvoiceService.java",
            "manifest_hash": "mh-1",
            "embedding_text": "invoice creation and tax calculation",
        },
    ]

    rebuild = store.rebuild(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
        vector_encoder=encoder,
    )
    loaded = store.load()
    results = store.search(query="payment timeout", embedding_provider=provider, top_k=2)

    assert rebuild["status"] == "ok"
    assert loaded["state"]["schema"] == "codecompass_vector_index.v2"
    assert loaded["state"]["vector_encoding_profile"]["mode"] == "int8"
    assert loaded["state"]["vector_encoding_compression_ratio"] > 1.0
    assert "encoded_vector" in loaded["entries"][0]
    assert "vector" not in loaded["entries"][0]
    assert len(results) == 2


def test_vector_store_refresh_rebuilds_when_encoding_changes(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    provider = FakeEmbeddingProvider(model_version="fake-v1", dimensions=4)
    documents = [{"record_id": "r1", "kind": "python_function", "file": "src/search.py", "embedding_text": "hybrid vector search"}]

    store.rebuild(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="int8", target_bits=8)),
    )
    unchanged = store.refresh(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="int8", target_bits=8)),
    )
    changed = store.refresh(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="float16", target_bits=16)),
    )

    assert unchanged["mode"] == "unchanged"
    assert changed["reason"] == "vector_encoding_changed"
    assert store.load()["state"]["vector_encoding_profile"]["mode"] == "float16"
