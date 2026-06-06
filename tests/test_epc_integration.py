"""EPC Integration tests — EPC-018.

Tests that EmbeddingProviderConfigService integrates correctly with:
- CodeCompassVectorEngine (EPC-009)
- SemanticOutputCorrection (EPC-008)
- EmbeddingTextBuilder (EPC-011)
- Index rebuild detection (EPC-012)
"""
import pytest
from pathlib import Path
import tempfile
import json

from worker.retrieval.embedding_provider import HashEmbeddingProvider, FakeEmbeddingProvider
from worker.retrieval.embedding_text_builder import (
    build_embedding_text,
    build_query_embedding_text,
    build_embedding_texts_batch,
)
from worker.retrieval.index_builder import provider_changed_since_last_build, build_incremental_index


# ── EPC-011: EmbeddingTextBuilder ────────────────────────────────────────────

def test_build_embedding_text_uses_explicit_field():
    doc = {"embedding_text": "explicit text", "content": "other content"}
    assert build_embedding_text(doc) == "explicit text"


def test_build_embedding_text_uses_text_fields():
    doc = {
        "text_fields": {
            "symbol_text": "MyClass",
            "summary_text": "does something",
            "content_text": "content here",
        }
    }
    result = build_embedding_text(doc)
    assert "MyClass" in result
    assert "does something" in result


def test_build_embedding_text_falls_back_to_text_field():
    doc = {"text": "plain text content"}
    assert build_embedding_text(doc) == "plain text content"


def test_build_embedding_text_returns_empty_for_empty_doc():
    assert build_embedding_text({}) == ""


def test_build_embedding_text_truncates_at_max_chars():
    long_text = "x" * 5000
    result = build_embedding_text({"embedding_text": long_text})
    assert len(result) <= 4096


def test_build_query_embedding_text_cleans_whitespace():
    result = build_query_embedding_text("  normalize  this   query  ")
    assert result == "normalize this query"


def test_build_embedding_texts_batch():
    docs = [
        {"text": "doc one"},
        {"embedding_text": "doc two explicit"},
        {},
    ]
    results = build_embedding_texts_batch(docs)
    assert len(results) == 3
    assert results[0] == "doc one"
    assert results[1] == "doc two explicit"
    assert results[2] == ""


# ── EPC-012: Index rebuild detection ─────────────────────────────────────────

def test_provider_changed_returns_false_when_no_previous_state():
    provider = HashEmbeddingProvider()
    assert not provider_changed_since_last_build(previous_state=None, current_provider=provider)


def test_provider_changed_returns_false_when_same_provider():
    provider = HashEmbeddingProvider()
    state = {"embedding_provider": "local_hash", "embedding_model_version": "hash-v1"}
    assert not provider_changed_since_last_build(previous_state=state, current_provider=provider)


def test_provider_changed_returns_true_when_model_version_changed():
    provider = HashEmbeddingProvider(model_version="hash-v2")
    state = {"embedding_provider": "local_hash", "embedding_model_version": "hash-v1"}
    assert provider_changed_since_last_build(previous_state=state, current_provider=provider)


def test_provider_changed_returns_true_when_provider_id_changed():
    provider = FakeEmbeddingProvider()  # provider_id="fake_test"
    state = {"embedding_provider": "local_hash", "embedding_model_version": "fake-v1"}
    assert provider_changed_since_last_build(previous_state=state, current_provider=provider)


def test_build_incremental_index_full_rebuild_on_provider_change():
    files = {"main.py": "def hello(): pass"}
    provider_v1 = HashEmbeddingProvider(model_version="hash-v1")
    # First build
    result_v1 = build_incremental_index(
        files=files,
        embedding_provider=provider_v1,
    )
    assert len(result_v1["entries"]) == 1

    # Simulate provider change — should force full rebuild ignoring previous entries
    provider_v2 = HashEmbeddingProvider(model_version="hash-v2")
    result_v2 = build_incremental_index(
        files=files,
        previous_entries=result_v1["entries"],
        previous_path_hashes=result_v1["state"]["path_hashes"],
        previous_state=result_v1["state"],
        embedding_provider=provider_v2,
    )
    # Provider changed → full rebuild → new entry produced
    assert result_v2["state"]["embedding_model_version"] == "hash-v2"
    assert len(result_v2["entries"]) == 1  # rebuilt


def test_build_incremental_index_no_rebuild_same_provider():
    files = {"main.py": "def hello(): pass"}
    provider = HashEmbeddingProvider()
    result_v1 = build_incremental_index(files=files, embedding_provider=provider)
    path_hashes = result_v1["state"]["path_hashes"]

    result_v2 = build_incremental_index(
        files=files,
        previous_entries=result_v1["entries"],
        previous_path_hashes=path_hashes,
        previous_state=result_v1["state"],
        embedding_provider=provider,
    )
    # No file change and same provider → no new entries produced (delta may still list paths if hash compare finds match)
    # Key assertion: provider was NOT changed, so full rebuild was NOT triggered
    assert result_v2["state"]["embedding_model_version"] == provider.model_version
    # same file content → delta shows no changes (path hash unchanged)
    assert result_v2["delta"]["changed_paths"] == []


# ── EPC-009: CodeCompassVectorEngine.build_from_config ───────────────────────

def test_vector_engine_build_from_config_uses_hash_provider_by_default():
    from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
    from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        store = CodeCompassVectorStore(index_path=f"{tmpdir}/index.json")
        engine = CodeCompassVectorEngine.build_from_config(store)
        assert isinstance(engine._embedding_provider, HashEmbeddingProvider)


def test_vector_engine_build_from_config_accepts_provider_config():
    from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
    from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        store = CodeCompassVectorStore(index_path=f"{tmpdir}/index.json")
        engine = CodeCompassVectorEngine.build_from_config(
            store, provider_config={"provider": "local_hash", "dimensions": 8}
        )
        assert isinstance(engine._embedding_provider, HashEmbeddingProvider)


# ── EPC-008: Semantic output correction uses config service ──────────────────

def test_semantic_correction_policy_normalization():
    from worker.coding.semantic_output_correction import normalize_semantic_correction_policy
    policy = normalize_semantic_correction_policy({
        "enabled": True,
        "embedding_provider": {"provider": "local_hash", "dimensions": 8},
        "fields": {"risk_classification": {"enabled": True}},
    })
    assert policy["enabled"] is True
    # provider normalized through config service → "local_hash" or "local" both valid
    assert policy["embedding_provider"]["provider"] in {"local", "local_hash"}


def test_semantic_correction_runs_with_hash_provider():
    from worker.coding.semantic_output_correction import correct_semantic_enum_fields
    policy = {
        "enabled": True,
        "similarity_threshold": 0.5,
        "min_margin": 0.0,
        "lexical_weight": 0.5,
        "embedding_provider": {"provider": "local_hash", "dimensions": 12},
        "fields": {
            "risk_classification": {
                "enabled": True,
                "candidates": ["low", "medium", "high", "critical"],
            }
        },
    }
    payload = {"risk_classification": "medium"}
    result, report = correct_semantic_enum_fields(payload=payload, policy=policy)
    assert isinstance(result, dict)
    assert "risk_classification" in result
