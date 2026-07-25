from __future__ import annotations

import pytest

from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchPort,
    VectorSearchQuery,
    VectorStore,
    VectorStoreDimensionsMismatch,
    VectorStoreFilters,
)


def _point(record_id: str, vector: tuple[float, ...], *, workspace: str = "ws-1", file: str = "src/a.py"):
    return PreparedVectorPoint(
        record_id=record_id,
        vector=vector,
        scope=VectorScope(workspace_id=workspace, repository_id="repo-1"),
        payload={
            "kind": "function",
            "file": file,
            "source_scope": "repo",
            "profile_name": "default",
            "role_labels": ["service"],
        },
    )


def test_json_backend_satisfies_runtime_contract_and_filters_before_ranking(tmp_path) -> None:
    store = JsonVectorStore(index_path=tmp_path / "index.json")
    assert isinstance(store, VectorSearchPort)
    assert isinstance(store, VectorStore)
    store.rebuild(
        [
            _point("a", (1.0, 0.0), file="src/a.py"),
            _point("b", (1.0, 0.0), workspace="ws-2", file="src/b.py"),
            _point("c", (0.0, 1.0), file="docs/c.md"),
        ],
        compatibility=CompatibilitySpec(dimensions=2),
    )

    result = store.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0),
            top_k=5,
            scope=VectorScope(workspace_id="ws-1", repository_id="repo-1"),
            filters=VectorStoreFilters(file_prefix="src"),
        )
    )

    assert [hit.record_id for hit in result.hits] == ["a"]
    assert result.effective_provider == "json"


def test_json_backend_dimension_mismatch_is_fail_closed_and_diagnostic(tmp_path) -> None:
    store = JsonVectorStore(index_path=tmp_path / "index.json")
    store.rebuild([_point("a", (1.0, 0.0))], compatibility=CompatibilitySpec(dimensions=2))

    with pytest.raises(VectorStoreDimensionsMismatch, match="dimensions_mismatch"):
        store.search_by_vector(VectorSearchQuery(query_vector=(1.0, 0.0, 0.0)))

    diagnostic = store.diagnostics()
    assert diagnostic.status == "degraded"
    assert diagnostic.reason == "dimensions_mismatch"
    assert diagnostic.details["expected_dimensions"] == 2


def test_json_backend_reads_legacy_state_as_degraded_and_writes_atomically(tmp_path) -> None:
    path = tmp_path / "index.json"
    path.write_text('{"entries":[{"record_id":"legacy","vector":[1.0,0.0]}]}', encoding="utf-8")
    store = JsonVectorStore(index_path=path)

    assert store.load()["entries"][0]["record_id"] == "legacy"
    assert store.diagnostics().reason == "migration_required"
    store.rebuild([_point("new", (1.0, 0.0))], compatibility=CompatibilitySpec(dimensions=2))

    assert not list(tmp_path.glob(".index.json.*.tmp"))
    assert store.diagnostics().status == "ready"
