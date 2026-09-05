from __future__ import annotations

import json

from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
)


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        dimensions=2,
        provider="test",
        model="fixed",
        profile="default",
        config_hash="config",
        manifest_hash="manifest",
    )


def test_search_reuses_unchanged_decoded_index(tmp_path, monkeypatch) -> None:
    scope = VectorScope("workspace", "repository", "default")
    store = JsonVectorStore(index_path=tmp_path / "index.json")
    store.rebuild(
        (
            PreparedVectorPoint(
                record_id="record-1",
                vector=(1.0, 0.0),
                scope=scope,
                payload={"kind": "test"},
                source_hash="source-1",
            ),
        ),
        compatibility=_compatibility(),
    )
    original_loads = json.loads
    calls = 0

    def recording_loads(value):
        nonlocal calls
        calls += 1
        return original_loads(value)

    monkeypatch.setattr(json, "loads", recording_loads)
    query = VectorSearchQuery(
        (1.0, 0.0),
        top_k=1,
        scope=scope,
        compatibility=_compatibility(),
    )

    assert store.search_by_vector(query).reason == "ok"
    assert store.search_by_vector(query).reason == "ok"
    assert calls == 0


def test_search_invalidates_cache_after_external_replace(tmp_path) -> None:
    scope = VectorScope("workspace", "repository", "default")
    index_path = tmp_path / "index.json"
    store = JsonVectorStore(index_path=index_path)
    store.rebuild(
        (
            PreparedVectorPoint(
                record_id="record-1",
                vector=(1.0, 0.0),
                scope=scope,
                payload={"kind": "test"},
                source_hash="source-1",
            ),
        ),
        compatibility=_compatibility(),
    )
    replacement = json.loads(index_path.read_text(encoding="utf-8"))
    replacement["entries"][0]["record_id"] = "record-2"
    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
    replacement_path.replace(index_path)

    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0),
            top_k=1,
            scope=scope,
            compatibility=_compatibility(),
        )
    )

    assert [hit.record_id for hit in result.hits] == ["record-2"]
