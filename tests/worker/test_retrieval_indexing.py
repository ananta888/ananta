from __future__ import annotations

from worker.retrieval.chunking import split_into_chunks
from worker.retrieval.embedding_provider import HashEmbeddingProvider
from worker.retrieval.index_builder import build_incremental_index, compute_delta_set


def test_chunking_returns_deterministic_metadata() -> None:
    chunks = split_into_chunks(path="src/example.py", content="def run():\n    return 1\n")
    again = split_into_chunks(path="src/example.py", content="def run():\n    return 1\n")
    assert chunks == again
    assert chunks[0]["metadata"]["language"] == "python"
    assert chunks[0]["metadata"]["symbol_name"] == "run"


def test_incremental_index_only_rebuilds_changed_paths() -> None:
    provider = HashEmbeddingProvider()
    first = build_incremental_index(
        files={"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 1\n"},
        embedding_provider=provider,
    )
    second = build_incremental_index(
        files={"a.py": "def a():\n    return 2\n", "b.py": "def b():\n    return 1\n"},
        previous_entries=first["entries"],
        previous_path_hashes=first["state"]["path_hashes"],
        embedding_provider=provider,
    )
    assert second["delta"]["changed_paths"] == ["a.py"]
    assert second["delta"]["deleted_paths"] == []
    assert len(second["entries"]) >= 2
    assert second["state"]["embedding_model_version"] == provider.model_version


def test_delta_set_marks_rename_by_hash_match() -> None:
    delta = compute_delta_set(
        previous_path_hashes={"old.py": "h1"},
        files={"new.py": "same"},
    )
    assert "old.py" in delta.deleted_paths
    assert "new.py" in delta.changed_paths

