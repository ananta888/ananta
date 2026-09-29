from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import pytest

from worker.retrieval import json_vector_store as json_vector_store_module
from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorIndexWritePlan,
    VectorIndexWriter,
    VectorScope,
    VectorSearchPort,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStore,
    VectorStoreDiagnostic,
    VectorStoreDiagnosticsPort,
    VectorStoreDimensionsMismatch,
    VectorStoreError,
    VectorStoreFilters,
    VectorStoreLifecycle,
)


def _point(
    record_id: str,
    vector: tuple[float, ...],
    *,
    workspace: str = "ws-1",
    file: str = "src/a.py",
) -> PreparedVectorPoint:
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
        source_hash=f"hash-{workspace}-{record_id}",
    )


def _write_result(mode: str = "test") -> IndexWriteResult:
    return IndexWriteResult(
        status="ok",
        mode=mode,
        reason=mode,
        indexed_documents=0,
    )


class _SearchOnlyFake:
    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        return VectorSearchResult(hits=(), reason=f"top_k:{query.top_k}")


class _DiagnosticsOnlyFake:
    def diagnostics(self) -> VectorStoreDiagnostic:
        return VectorStoreDiagnostic(
            status="ready",
            reason="ok",
            provider="fake",
            backend_version="fake.v1",
        )


class _LifecycleOnlyFake:
    def close(self) -> None:
        return None


class _WriterMissingDeleteScopeFake:
    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return _write_result("rebuild")

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return _write_result("refresh")

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        return _write_result("upsert")

    def delete(
        self,
        point_ids: Sequence[str],
        *,
        scope: VectorScope,
    ) -> IndexWriteResult:
        return _write_result("delete")


class _SignatureIncompatibleSearchFake:
    def search_by_vector(
        self,
        query: VectorSearchQuery,
        *,
        required_backend_flag: bool,
    ) -> VectorSearchResult:
        return VectorSearchResult(
            hits=(),
            reason=f"{query.top_k}:{required_backend_flag}",
        )


class _SignatureIncompatibleWriterFake:
    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        backend_specific_compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return _write_result("rebuild")

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return _write_result("refresh")

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        return _write_result("upsert")

    def delete(
        self,
        point_ids: Sequence[str],
        *,
        scope: VectorScope,
    ) -> IndexWriteResult:
        return _write_result("delete")

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        return _write_result("delete")


def _consume_search(port: VectorSearchPort) -> VectorSearchResult:
    return port.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0),
            scope=VectorScope("contract-workspace", "contract-repository"),
        )
    )


def _consume_rebuild(port: VectorIndexWriter) -> IndexWriteResult:
    return port.rebuild(
        [_point("contract", (1.0, 0.0))],
        compatibility=CompatibilitySpec(dimensions=2),
    )


def _seed_index(path) -> bytes:
    original = b'{"state":{"schema":"legacy.v1"},"entries":[]}\n'
    path.write_bytes(original)
    return original


def _assert_failed_atomic_write_left_no_trace(path, original: bytes) -> None:
    assert path.read_bytes() == original
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_index_write_result_additively_exposes_accepted_count() -> None:
    result = IndexWriteResult(
        status="partial",
        mode="upsert",
        reason="qdrant_unavailable",
        indexed_documents=1,
        accepted=3,
        upserted=1,
        skipped=1,
        failed=1,
    )

    assert result.as_dict()["accepted"] == 3


@pytest.mark.parametrize("batch_size", [True, "128", 128.0, 0, 1001])
def test_vector_index_write_plan_rejects_invalid_batch_sizes(
    batch_size: object,
) -> None:
    with pytest.raises(ValueError, match="vector_batch_size_invalid"):
        VectorIndexWritePlan(batch_size=batch_size)


def test_narrow_capability_fakes_are_structurally_substitutable_without_composite_coupling() -> None:
    search = _SearchOnlyFake()
    diagnostics = _DiagnosticsOnlyFake()
    lifecycle = _LifecycleOnlyFake()

    assert isinstance(search, VectorSearchPort)
    assert not isinstance(search, VectorIndexWriter)
    assert not isinstance(search, VectorStore)
    assert _consume_search(search).reason == "top_k:10"

    assert isinstance(diagnostics, VectorStoreDiagnosticsPort)
    assert not isinstance(diagnostics, VectorSearchPort)
    assert not isinstance(diagnostics, VectorStore)

    assert isinstance(lifecycle, VectorStoreLifecycle)
    assert not isinstance(lifecycle, VectorStoreDiagnosticsPort)
    assert not isinstance(lifecycle, VectorStore)


def test_writer_fake_missing_required_method_is_not_structurally_substitutable() -> None:
    fake = _WriterMissingDeleteScopeFake()

    assert not isinstance(fake, VectorIndexWriter)
    assert not isinstance(fake, VectorStore)


def test_search_fake_with_incompatible_signature_fails_at_consumer_boundary() -> None:
    fake = _SignatureIncompatibleSearchFake()

    # runtime_checkable protocols inspect member presence; the actual port call
    # is the runtime boundary that rejects a structurally named but incompatible fake.
    assert isinstance(fake, VectorSearchPort)
    with pytest.raises(TypeError, match="required_backend_flag"):
        _consume_search(fake)


def test_writer_fake_with_incompatible_signature_fails_at_consumer_boundary() -> None:
    fake = _SignatureIncompatibleWriterFake()

    assert isinstance(fake, VectorIndexWriter)
    with pytest.raises(TypeError, match="compatibility"):
        _consume_rebuild(fake)


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
        store.search_by_vector(
            VectorSearchQuery(
                query_vector=(1.0, 0.0, 0.0),
                scope=VectorScope("ws-1", "repo-1"),
            )
        )

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


def test_json_backend_reports_missing_index_before_distance_incompatibility(tmp_path) -> None:
    store = JsonVectorStore(index_path=tmp_path / "missing.json")
    compatibility = CompatibilitySpec(dimensions=2, distance="dot")

    assert store.compatibility_reason(compatibility) == "missing_index"
    result = store.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0),
            scope=VectorScope("ws-1", "repo-1"),
            compatibility=compatibility,
        ),
    )

    assert result.reason == "missing_index"
    assert result.diagnostics["compatibility_reason"] == "missing_index"


def test_json_backend_rejects_unscoped_search_before_reading_tenant_entries(
    tmp_path,
) -> None:
    store = JsonVectorStore(index_path=tmp_path / "index.json")
    store.rebuild(
        [
            _point("workspace-a", (1.0, 0.0), workspace="ws-1"),
            _point("workspace-b", (1.0, 0.0), workspace="ws-2"),
        ],
        compatibility=CompatibilitySpec(dimensions=2),
    )

    result = store.search_by_vector(
        VectorSearchQuery(query_vector=(1.0, 0.0)),
    )

    assert result.hits == ()
    assert result.reason == "vector_scope_required"
    assert result.diagnostics == {
        "status": "degraded",
        "reason": "vector_scope_required",
    }


def test_json_backend_write_failure_preserves_old_file_and_cleans_temp(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "index.json"
    original = _seed_index(path)
    store = JsonVectorStore(index_path=path)

    def _fail_during_write(payload: Any, handle: Any, **kwargs: Any) -> None:
        del payload, kwargs
        handle.write('{"partial":')
        raise OSError("injected_write_failure")

    monkeypatch.setattr(json_vector_store_module.json, "dump", _fail_during_write)

    with pytest.raises(OSError, match="injected_write_failure"):
        store.save(state={"schema": "next.v1"}, entries=[])

    _assert_failed_atomic_write_left_no_trace(path, original)


def test_json_backend_file_fsync_failure_preserves_old_file_and_cleans_temp(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "index.json"
    original = _seed_index(path)
    store = JsonVectorStore(index_path=path)

    def _fail_fsync(file_descriptor: int) -> None:
        del file_descriptor
        raise OSError("injected_fsync_failure")

    monkeypatch.setattr(json_vector_store_module.os, "fsync", _fail_fsync)

    with pytest.raises(OSError, match="injected_fsync_failure"):
        store.save(state={"schema": "next.v1"}, entries=[])

    _assert_failed_atomic_write_left_no_trace(path, original)


def test_json_backend_replace_failure_preserves_old_file_and_cleans_temp(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "index.json"
    original = _seed_index(path)
    store = JsonVectorStore(index_path=path)

    def _fail_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        del source, destination
        raise OSError("injected_replace_failure")

    monkeypatch.setattr(json_vector_store_module.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="injected_replace_failure"):
        store.save(state={"schema": "next.v1"}, entries=[])

    _assert_failed_atomic_write_left_no_trace(path, original)


def test_json_backend_directory_fsync_failure_is_best_effort_after_atomic_replace(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "index.json"
    _seed_index(path)
    store = JsonVectorStore(index_path=path)
    real_fsync = json_vector_store_module.os.fsync
    calls = 0

    def _fail_directory_fsync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected_directory_fsync_failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(json_vector_store_module.os, "fsync", _fail_directory_fsync)

    store.save(state={"schema": "next.v1"}, entries=[])

    assert json.loads(path.read_text(encoding="utf-8"))["state"]["schema"] == "next.v1"
    assert calls == 2
    assert not list(tmp_path.glob(".index.json.*.tmp"))


def test_json_backend_deletes_only_the_requested_scope(tmp_path) -> None:
    store = JsonVectorStore(index_path=tmp_path / "index.json")
    scope_a = VectorScope("ws-1", "repo-1")
    scope_b = VectorScope("ws-2", "repo-1")
    store.rebuild(
        [
            _point("a", (1.0, 0.0), workspace="ws-1"),
            _point("b", (0.0, 1.0), workspace="ws-2"),
        ],
        compatibility=CompatibilitySpec(dimensions=2),
    )

    deleted = store.delete_scope(scope_a)

    assert deleted.deleted == 1
    remaining = store.search_by_vector(
        VectorSearchQuery((0.0, 1.0), scope=scope_b)
    )
    assert [hit.record_id for hit in remaining.hits] == ["b"]


@pytest.mark.parametrize("batch_size", [True, "128", 128.0, 0, 1001])
def test_json_backend_rejects_invalid_batch_sizes(
    tmp_path,
    batch_size: object,
) -> None:
    store = JsonVectorStore(index_path=tmp_path / "index.json")

    with pytest.raises(VectorStoreError, match="vector_batch_size_invalid"):
        store.upsert(
            [_point("a", (1.0, 0.0))],
            batch_size=batch_size,
        )
