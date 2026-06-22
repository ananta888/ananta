"""VEC-DELTA-003 / TQ-026 — Backward compatibility tests for CodeCompassVectorStore.

These tests verify that:
- old index files without encoded_vector load correctly
- indexes missing schema/state fields load without crash
- rebuild writes the expected storage format for off vs int8 modes
- refresh correctly detects encoding-config changes and triggers rebuild
- save/load round-trips are lossless
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.vector_encoding import (
    VectorEncoder,
    VectorEncodingProfile,
)


# ---------------------------------------------------------------------------
# Fake embedding provider — deterministic, no external calls
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
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _minimal_documents(n: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "record_id": f"r{i}",
            "kind": "python_function",
            "file": f"src/module_{i}.py",
            "manifest_hash": "mh-test",
            "embedding_text": f"embedding text for document {i}",
        }
        for i in range(n)
    ]


def _write_raw_index(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# TQ-026-001  old index with only `vector` field (no encoded_vector) loads
# ---------------------------------------------------------------------------

def test_load_old_index_without_encoded_vector(tmp_path):
    """An index written without encoded_vector (pre-v2 schema) must load cleanly."""
    index_path = tmp_path / "cc_vector_index.json"
    old_entry = {
        "record_id": "r1",
        "kind": "java_method",
        "file": "src/OldService.java",
        "vector": [0.1, 0.2, 0.3, 0.4],
        "embedding_text": "old style entry",
    }
    _write_raw_index(index_path, {"state": {"schema": "old_v1"}, "entries": [old_entry]})

    store = CodeCompassVectorStore(index_path=index_path)
    loaded = store.load()
    assert len(loaded["entries"]) == 1
    assert loaded["entries"][0]["record_id"] == "r1"
    assert "vector" in loaded["entries"][0]


# ---------------------------------------------------------------------------
# TQ-026-002  index without schema field still loads (degraded state)
# ---------------------------------------------------------------------------

def test_load_v1_schema_falls_back_to_degraded(tmp_path):
    """An index with no schema in state must not crash; entries are preserved."""
    index_path = tmp_path / "cc_vector_index.json"
    _write_raw_index(
        index_path,
        {
            "state": {"manifest_hash": "mh-legacy"},
            "entries": [
                {"record_id": "r1", "kind": "function", "file": "a.py", "vector": [0.5, 0.5]}
            ],
        },
    )
    store = CodeCompassVectorStore(index_path=index_path)
    loaded = store.load()
    assert loaded["state"]["manifest_hash"] == "mh-legacy"
    assert len(loaded["entries"]) == 1


# ---------------------------------------------------------------------------
# TQ-026-003  rebuild with mode=off writes `vector` key
# ---------------------------------------------------------------------------

def test_rebuild_with_encoding_off_writes_vector_field(tmp_path):
    """mode=off (disabled) must write `vector` directly onto each entry."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()
    encoder = VectorEncoder(VectorEncodingProfile(mode="off"))

    store.rebuild(
        documents=_minimal_documents(2),
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=encoder,
    )
    loaded = store.load()
    for entry in loaded["entries"]:
        assert "vector" in entry, "mode=off must write 'vector' key"


# ---------------------------------------------------------------------------
# TQ-026-004  rebuild with mode=int8 writes `encoded_vector` key
# ---------------------------------------------------------------------------

def test_rebuild_with_encoding_int8_writes_encoded_vector(tmp_path):
    """mode=int8 must write `encoded_vector` and omit the raw `vector` float list."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()
    encoder = VectorEncoder(VectorEncodingProfile(mode="int8"))

    store.rebuild(
        documents=_minimal_documents(2),
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=encoder,
    )
    loaded = store.load()
    for entry in loaded["entries"]:
        assert "encoded_vector" in entry, "mode=int8 must write 'encoded_vector' key"
        assert "vector" not in entry, "mode=int8 must not write raw 'vector' key"


# ---------------------------------------------------------------------------
# TQ-026-005  refresh detects encoding change and rebuilds
# ---------------------------------------------------------------------------

def test_refresh_detects_encoding_change_and_rebuilds(tmp_path):
    """Switching from mode=off to mode=int8 must trigger a rebuild."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()
    docs = _minimal_documents(1)

    store.rebuild(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="off")),
    )
    result = store.refresh(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="int8")),
    )
    assert result["reason"] == "vector_encoding_changed"
    assert result["mode"] == "rebuild"


# ---------------------------------------------------------------------------
# TQ-026-006  refresh with unchanged config returns no rebuild
# ---------------------------------------------------------------------------

def test_refresh_unchanged_returns_no_rebuild(tmp_path):
    """Calling refresh with identical config after rebuild must return 'unchanged'."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    provider = FakeEmbeddingProvider()
    docs = _minimal_documents(1)
    encoder = VectorEncoder(VectorEncodingProfile(mode="int8"))

    store.rebuild(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=encoder,
    )
    result = store.refresh(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="cs-1",
        manifest_hash="mh-1",
        vector_encoder=VectorEncoder(VectorEncodingProfile(mode="int8")),
    )
    assert result["mode"] == "unchanged"
    assert result["reason"] == "unchanged"


# ---------------------------------------------------------------------------
# TQ-026-007  save then load round-trip returns same entries
# ---------------------------------------------------------------------------

def test_store_save_load_roundtrip(tmp_path):
    """save() followed by load() must return exactly the same entries."""
    store = CodeCompassVectorStore(index_path=tmp_path / "index.json")
    entries = [
        {"record_id": "r1", "kind": "function", "file": "a.py", "vector": [1.0, 2.0]},
        {"record_id": "r2", "kind": "class", "file": "b.py", "vector": [3.0, 4.0]},
    ]
    state = {"schema": "codecompass_vector_index.v2", "manifest_hash": "mh-roundtrip"}

    store.save(state=state, entries=entries)
    loaded = store.load()

    assert loaded["state"]["manifest_hash"] == "mh-roundtrip"
    assert len(loaded["entries"]) == 2
    assert loaded["entries"][0]["record_id"] == "r1"
    assert loaded["entries"][1]["vector"] == [3.0, 4.0]


# ---------------------------------------------------------------------------
# TQ-026-008  index with only entries (no state field) loads without crash
# ---------------------------------------------------------------------------

def test_old_index_missing_state_field(tmp_path):
    """An index JSON with no 'state' key at all must load without crash."""
    index_path = tmp_path / "cc_vector_index.json"
    _write_raw_index(
        index_path,
        {
            "entries": [
                {"record_id": "r1", "kind": "function", "file": "x.py", "vector": [0.1, 0.9]}
            ]
        },
    )
    store = CodeCompassVectorStore(index_path=index_path)
    loaded = store.load()
    # state must be a dict (even if empty)
    assert isinstance(loaded["state"], dict)
    assert len(loaded["entries"]) == 1
    assert loaded["entries"][0]["record_id"] == "r1"
