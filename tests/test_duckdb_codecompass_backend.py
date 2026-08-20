from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.duckdb_connection_factory import DuckDBConnectionFactory
from worker.retrieval.duckdb_extension_policy import DuckDBPolicyError, assert_safe_sql
from worker.retrieval.duckdb_query_templates import TEMPLATES
from worker.retrieval.duckdb_vector_store import DuckDBVectorStore
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_config import VectorStoreConfig, VectorStoreConfigError, VectorStoreProvider
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
    VectorStoreError,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory
from worker.retrieval.codecompass_duckdb_materializer import CodeCompassDuckDBMaterializer
from agent.services.codecompass_duckdb_analytics_service import CodeCompassDuckDBAnalyticsService


def _scope() -> VectorScope:
    return VectorScope(workspace_id="ws-1", repository_id="repo-1", profile_name="default", domain="codecompass")


def test_config_accepts_duckdb_and_rejects_vss_and_free_sql() -> None:
    config = VectorStoreConfig.from_mapping({"provider": "duckdb", "duckdb": {}})
    assert config.provider == VectorStoreProvider.DUCKDB
    with pytest.raises(VectorStoreConfigError, match="vss_default_forbidden"):
        DuckDBVectorStoreConfig.from_mapping({"vector_search": {"mode": "exact", "vss": {"enabled": True}}})
    with pytest.raises(VectorStoreConfigError, match="free_sql_network_or_attach"):
        DuckDBVectorStoreConfig.from_mapping({"security": {"free_form_sql": True}})
    with pytest.raises(VectorStoreConfigError, match="autoinstall_or_autoload"):
        DuckDBVectorStoreConfig.from_mapping({"extensions": {"autoinstall_known_extensions": True}})


def test_sql_policy_rejects_attach_and_install() -> None:
    with pytest.raises(DuckDBPolicyError):
        assert_safe_sql("ATTACH 'other.db'")
    with pytest.raises(DuckDBPolicyError):
        assert_safe_sql("INSTALL httpfs")
    for sql in TEMPLATES.values():
        assert_safe_sql(sql)


@pytest.mark.skipif(not DuckDBConnectionFactory.available(), reason="duckdb extra missing")
def test_factory_and_exact_search_roundtrip(tmp_path) -> None:
    config = DuckDBVectorStoreConfig(snapshot_root=tmp_path / "duckdb")
    store = VectorStoreFactory().create(
        VectorStoreConfig(provider=VectorStoreProvider.DUCKDB, duckdb=config)
    )
    scope = _scope()
    points = [
        PreparedVectorPoint(
            record_id="pay",
            vector=(1.0, 0.0, 0.0),
            scope=scope,
            payload={"path": "src/payment.py", "kind": "python_class", "symbol": "PaymentService"},
            source_hash="hash-pay",
        ),
        PreparedVectorPoint(
            record_id="auth",
            vector=(0.0, 1.0, 0.0),
            scope=scope,
            payload={"path": "src/auth.py", "kind": "python_class", "symbol": "AuthService"},
            source_hash="hash-auth",
        ),
    ]
    written = store.rebuild(points, compatibility=CompatibilitySpec(dimensions=3, provider="duckdb", manifest_hash="m1"))
    assert written.status == "ok"
    result = store.search_by_vector(
        VectorSearchQuery(query_vector=(1.0, 0.0, 0.0), top_k=1, scope=scope)
    )
    assert result.hits[0].record_id == "pay"
    assert result.diagnostics["mode"] == "exact"
    escaped = store.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0, 0.0),
            top_k=1,
            scope=VectorScope(workspace_id="other", repository_id="repo-1"),
        )
    )
    assert escaped.hits == ()


@pytest.mark.skipif(not DuckDBConnectionFactory.available(), reason="duckdb extra missing")
def test_materializer_and_analytics_templates(tmp_path) -> None:
    config = DuckDBVectorStoreConfig(snapshot_root=tmp_path / "duckdb")
    materializer = CodeCompassDuckDBMaterializer(config)
    scope = _scope()
    materializer.materialize(
        records=[
            {"id": "n1", "path": "src/a.py", "kind": "python_function", "symbol": "run", "text": "def run(): pass"},
            {"id": "n2", "path": "src/b.py", "kind": "python_class", "symbol": "Svc", "text": "class Svc: pass"},
        ],
        scope=scope,
        manifest_hash="abc",
        compatibility_fingerprint="fp1",
        source_revision="rev-1",
    )
    service = CodeCompassDuckDBAnalyticsService(config)
    counts = service.query(
        "document_counts_by_kind",
        capability={
            "workspace_id": "ws-1",
            "repository_id": "repo-1",
            "profile_name": "default",
            "domain": "codecompass",
        },
    )
    assert counts["count"] >= 1
    with pytest.raises(VectorStoreError, match="unknown_query_template"):
        service.query("drop_all", capability={"workspace_id": "ws-1", "repository_id": "repo-1"})
    with pytest.raises(VectorStoreError, match="empty_scope"):
        service.query("document_counts_by_kind", capability=None)


def test_example_config_is_strict() -> None:
    payload = json.loads(Path("config/examples/vector-store.duckdb-local.json").read_text())
    config = VectorStoreConfig.from_mapping(payload)
    assert config.provider == VectorStoreProvider.DUCKDB
    assert config.duckdb.vss_enabled is False


@pytest.mark.skipif(not DuckDBConnectionFactory.available(), reason="duckdb extra missing")
def test_scoped_pointers_and_upsert_preserve_existing_points(tmp_path) -> None:
    config = DuckDBVectorStoreConfig(snapshot_root=tmp_path / "duckdb")
    store = DuckDBVectorStore(config=config)
    first_scope = _scope()
    second_scope = VectorScope(workspace_id="ws-2", repository_id="repo-2", profile_name="default", domain="codecompass")
    compatibility = CompatibilitySpec(dimensions=2, provider="duckdb", manifest_hash="m1")
    first = PreparedVectorPoint(record_id="first", vector=(1.0, 0.0), scope=first_scope, payload={"path": "a.py"}, source_hash="a")
    second = PreparedVectorPoint(record_id="second", vector=(0.0, 1.0), scope=first_scope, payload={"path": "b.py"}, source_hash="b")
    foreign = PreparedVectorPoint(record_id="foreign", vector=(1.0, 0.0), scope=second_scope, payload={"path": "a.py"}, source_hash="c")
    store.rebuild([first], compatibility=compatibility)
    store.upsert([second])
    store.rebuild([foreign], compatibility=compatibility)
    result = store.search_by_vector(VectorSearchQuery(query_vector=(1.0, 0.0), top_k=10, scope=first_scope))
    assert {hit.record_id for hit in result.hits} == {"first", "second"}


@pytest.mark.skipif(not DuckDBConnectionFactory.available(), reason="duckdb extra missing")
def test_delete_and_delete_scope_are_idempotent_on_empty_snapshot(tmp_path) -> None:
    store = DuckDBVectorStore(
        config=DuckDBVectorStoreConfig(snapshot_root=tmp_path / "duckdb")
    )
    scope = _scope()
    point = PreparedVectorPoint(
        record_id="only",
        vector=(1.0, 0.0),
        scope=scope,
        payload={"path": "only.py"},
        source_hash="only",
    )
    store.rebuild(
        [point],
        compatibility=CompatibilitySpec(
            dimensions=2,
            provider="duckdb",
            manifest_hash="delete-idempotency",
        ),
    )

    first = store.delete(["only"], scope=scope)
    repeated = store.delete(["only"], scope=scope)
    scope_delete = store.delete_scope(scope)
    repeated_scope_delete = store.delete_scope(scope)

    assert first.status == "ok"
    assert first.diagnostics["deleted"] == 1
    assert repeated.reason == "empty"
    assert scope_delete.reason == "empty"
    assert repeated_scope_delete.reason == "empty"
    result = store.search_by_vector(
        VectorSearchQuery(query_vector=(1.0, 0.0), top_k=10, scope=scope)
    )
    assert result.hits == ()
