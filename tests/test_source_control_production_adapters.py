from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import AgentInfoDB, KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogService,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadService,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowService,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
    HubBoundSourceIndexSubmissionAdapter,
    HubSourceControlOperationsAdapter,
    ScopedWorkerModelDestinationCatalog,
    SourceControlProductionAdapterError,
    build_scoped_effective_access_service,
    derive_policy_snapshot_id,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelHealth,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@dataclass(frozen=True)
class _Model:
    provider_id: str = "ollama"
    model_id: str = "code-model"
    availability: ModelAvailability = ModelAvailability.AVAILABLE
    health: ModelHealth = ModelHealth.HEALTHY
    capabilities: tuple[str, ...] = ("class:code",)


class _GraphStore:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def load(self) -> dict:
        return self.payload

    def load_visual_metrics(self):
        return None


class _PassThroughGraphProjection:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def project(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "nodes": list(kwargs["nodes"]),
            "edges": list(kwargs["edges"]),
            "metadata": dict(kwargs["metadata"]),
            "diagnostics": dict(kwargs.get("diagnostics") or {}),
            "warnings": list(kwargs.get("warnings") or []),
        }


def _graph_adapter(
    payload: dict,
) -> tuple[HubSourceControlOperationsAdapter, _PassThroughGraphProjection]:
    projection = _PassThroughGraphProjection()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._graph_read = CodeCompassGraphReadService(
        projection=projection,
        window=CodeCompassGraphWindowService(),
        domains=CodeCompassGraphDomainCatalogService(),
    )
    adapter._active_index = (
        lambda **_kwargs: type("Index", (), {"id": "index-1"})()
    )
    store = _GraphStore(payload)
    adapter._graph_store = lambda _index: store
    adapter._artifact_projection = lambda _index: {"status": "verified"}
    return adapter, projection


def test_query_authorizes_only_the_hub_resolved_active_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class _Retrieval:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def search_records(self, query, **kwargs):
            calls.append({"query": query, **kwargs})
            return []

    index = type("Index", (), {"id": "index-authorized"})()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._active_index = lambda **_kwargs: index
    adapter._artifact_projection = lambda _index: {
        "state": "available",
        "knowledge_index_id": index.id,
    }
    monkeypatch.setattr(
        "agent.services.source_control_production_adapters."
        "KnowledgeIndexRetrievalService",
        _Retrieval,
    )

    result = adapter.query(
        parameters={"query": "organization source catalog", "limit": 5}
    )

    assert result["matches"] == []
    assert calls == [
        {
            "query": "organization source catalog",
            "limit": 5,
            "task_kind": "code_review",
            "retrieval_intent": "fuzzy_semantic",
            "allowed_index_ids": {"index-authorized"},
        }
    ]


def _domain_scope_key(
    payload: dict,
    *,
    path: str,
) -> str:
    service = CodeCompassGraphDomainCatalogService()
    catalog = service.catalog(
        nodes=[
            *list(payload.get("nodes") or []),
            *list(payload.get("semantic_nodes") or []),
        ]
    )
    return next(facet.key for facet in catalog.facets if facet.path == path)


def _scoped_graph_payload() -> dict:
    return {
        "state": {"manifest_hash": "sha256:graph-revision-1"},
        "nodes": [
            {"id": "sales-root", "domain_id": "sales"},
            {"id": "orders", "domain_id": "sales.orders"},
            {"id": "orders-api", "domain_id": "sales.orders.api"},
            {"id": "support", "domain_id": "support"},
        ],
        "edges": [
            {
                "edge_id": "sales-orders",
                "source_id": "sales-root",
                "target_id": "orders",
                "edge_type": "contains_domain",
            },
            {
                "edge_id": "orders-api",
                "source_id": "orders",
                "target_id": "orders-api",
                "edge_type": "contains_symbol",
            },
            {
                "edge_id": "domain-boundary",
                "source_id": "orders",
                "target_id": "support",
                "edge_type": "cross_reference",
            },
            {
                "edge_id": "cross-node-page",
                "source_id": "sales-root",
                "target_id": "orders-api",
                "edge_type": "depends_on",
            },
            {
                "edge_id": "dangling",
                "source_id": "support",
                "target_id": "missing",
                "edge_type": "missing_target",
            },
        ],
        "semantic_nodes": [
            {
                "id": "semantic:orders",
                "domain_id": "sales.orders",
                "kind": "semantic_node",
            }
        ],
        "semantic_edges": [
            {
                "edge_id": "semantic-orders",
                "source_id": "orders-api",
                "target_id": "semantic:orders",
                "edge_type": "semantic_declares",
            }
        ],
        "diagnostics": {
            "semantic_translation": {
                "status": "ready",
                "semantic_budget": {
                    "truncated": False,
                    "unresolved_edge_count": 0,
                },
            }
        },
    }


def test_active_graph_projects_codecompass_edge_identifiers() -> None:
    class _Store:
        def load(self):
            return {
                "state": {"manifest_hash": "sha256:graph"},
                "nodes": [{"id": "n1"}, {"id": "n2"}],
                "edges": [
                    {
                        "source_id": "n1",
                        "target_id": "n2",
                        "edge_type": "declares",
                    }
                ],
                "semantic_nodes": [{"id": "semantic:n1"}],
                "semantic_edges": [
                    {
                        "source_id": "n1",
                        "target_id": "semantic:n1",
                        "edge_type": "semantic_declares",
                    }
                ],
            }

        def load_visual_metrics(self):
            return None

    class _Projection:
        def __init__(self) -> None:
            self.nodes = []
            self.edges = []

        def project(self, **kwargs):
            self.nodes = list(kwargs["nodes"])
            self.edges = list(kwargs["edges"])
            return {"metadata": dict(kwargs["metadata"])}

    projection = _Projection()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._graph_read = CodeCompassGraphReadService(
        projection=projection,
        window=CodeCompassGraphWindowService(),
        domains=CodeCompassGraphDomainCatalogService(),
    )
    adapter._active_index = lambda **_kwargs: type("Index", (), {"id": "index-1"})()
    adapter._graph_store = lambda _index: _Store()
    adapter._artifact_projection = lambda _index: {"status": "verified"}

    result = adapter.graph(parameters={"limit": 100})

    assert projection.nodes == [{"id": "n1"}, {"id": "n2"}]
    assert projection.edges == [
        {
            "source_id": "n1",
            "target_id": "n2",
            "edge_type": "declares",
        }
    ]
    assert result["text_alternative"] == "Graph with 2 nodes and 1 edges."


def test_topology_graph_view_uses_injected_connected_window() -> None:
    class _Store:
        def load(self):
            return {
                "state": {"manifest_hash": "sha256:graph"},
                "nodes": [
                    {"id": "isolated"},
                    {"id": "leaf-b"},
                    {"id": "hub"},
                    {"id": "leaf-a"},
                ],
                "edges": [
                    {
                        "edge_id": "hub-a",
                        "source_id": "hub",
                        "target_id": "leaf-a",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "hub-b",
                        "source_id": "hub",
                        "target_id": "leaf-b",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "leaf-semantic",
                        "source_id": "leaf-a",
                        "target_id": "semantic:service",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "dangling",
                        "source_id": "hub",
                        "target_id": "semantic:missing",
                        "edge_type": "declares",
                    },
                ],
                "semantic_nodes": [{"id": "semantic:service"}],
                "semantic_edges": [
                    {
                        "edge_id": "semantic-hub",
                        "source_id": "semantic:service",
                        "target_id": "hub",
                        "edge_type": "implements",
                    }
                ],
                "diagnostics": {
                    "semantic_translation": {
                        "status": "degraded",
                        "reason": "semantic_graph_partial",
                        "semantic_budget": {
                            "configured_max_records_per_partition": 5000,
                            "max_records_per_partition": 5000,
                            "max_bytes_per_partition": 4194304,
                            "configuration_clamped": False,
                            "truncated": True,
                            "truncated_node_count": 3,
                            "truncated_edge_count": 1,
                            "unresolved_edge_count": 2,
                            "semantic_node_bytes": 100,
                            "semantic_edge_bytes": 50,
                        },
                    }
                },
            }

        def load_visual_metrics(self):
            return None

    class _Projection:
        def __init__(self) -> None:
            self.kwargs = {}

        def project(self, **kwargs):
            self.kwargs = kwargs
            return {"metadata": dict(kwargs["metadata"])}

    projection = _Projection()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._graph_read = CodeCompassGraphReadService(
        projection=projection,
        window=CodeCompassGraphWindowService(),
        domains=CodeCompassGraphDomainCatalogService(),
    )
    adapter._active_index = (
        lambda **_kwargs: type("Index", (), {"id": "index-1"})()
    )
    adapter._graph_store = lambda _index: _Store()
    adapter._artifact_projection = lambda _index: {"status": "verified"}

    result = adapter.graph(
        parameters={"limit": 4, "view": "topology", "max_edges": 4}
    )

    assert [node["id"] for node in projection.kwargs["nodes"]] == [
        "hub",
        "leaf-a",
        "semantic:service",
        "leaf-b",
    ]
    assert [edge["edge_id"] for edge in projection.kwargs["edges"]] == [
        "hub-a",
        "leaf-semantic",
        "semantic-hub",
        "hub-b",
    ]
    assert projection.kwargs["derive_projection_revision"] is True
    assert result["metadata"]["content_graph_revision"].startswith("sha256:")
    assert result["metadata"] == {
        "knowledge_index_id": "index-1",
        "view": "topology",
        "content_graph_revision": result["metadata"]["content_graph_revision"],
        "next_cursor": None,
        "total_nodes": 5,
        "total_edges": 4,
        "source_edge_count": 5,
        "unresolved_edge_count": 1,
        "internal_edge_count": 4,
        "edge_capped": False,
        "max_edges": 4,
        "semantic_budget": {
            "configured_max_records_per_partition": 5000,
            "max_records_per_partition": 5000,
            "max_bytes_per_partition": 4194304,
            "configuration_clamped": False,
            "truncated": True,
            "truncated_node_count": 3,
            "truncated_edge_count": 1,
            "unresolved_edge_count": 2,
            "semantic_node_bytes": 100,
            "semantic_edge_bytes": 50,
        },
        "domain_scope": None,
        "domain_scope_label": None,
        "include_subdomains": True,
        "global_total_nodes": 5,
        "global_total_edges": 4,
        "global_source_edge_count": 5,
        "global_unresolved_edge_count": 1,
        "scope_total_nodes": 5,
        "scope_boundary_edge_count": 0,
        "scope_unresolved_edge_count": 1,
        "remaining_nodes": 1,
        "window_node_limit": 4,
        "window_domain_group_count": 1,
        "scope_domain_group_count": 1,
        "delivery_complete": False,
    }
    assert result["text_alternative"] == (
        "Topology graph window with 4 nodes and 4 edges out of 5 nodes."
    )
    assert projection.kwargs["warnings"] == [
        "1 graph relation has an unavailable source or target node. The staged "
        "edge stream retains these relations; reindex the source to materialize "
        "current endpoints.",
        "The semantic graph reached its configured record budget; the topology "
        "is a documented partial view.",
        "2 semantic graph relations were not materialized because no "
        "source-grounded endpoint was available.",
    ]
    assert projection.kwargs["diagnostics"]["semantic_translation"]["status"] == (
        "degraded"
    )


def test_graph_inventory_paginates_complete_domain_tree_and_coverage() -> None:
    adapter, _projection = _graph_adapter(_scoped_graph_payload())

    first = adapter.graph(parameters={"view": "inventory", "limit": 2})
    second = adapter.graph(
        parameters={
            "view": "inventory",
            "limit": 2,
            "cursor": first["metadata"]["next_cursor"],
        }
    )

    assert first["schema"] == "codecompass_graph_inventory.v1"
    assert [item["path"] for item in first["facets"]["domains"]["items"]] == [
        "sales",
        "sales.orders",
    ]
    assert [item["path"] for item in second["facets"]["domains"]["items"]] == [
        "sales.orders.api",
        "support",
    ]
    sales, orders = first["facets"]["domains"]["items"]
    assert sales["parent_key"] is None
    assert sales["direct_node_count"] == 1
    assert sales["subtree_node_count"] == 4
    assert sales["has_children"] is True
    assert orders["parent_key"] == sales["key"]
    assert orders["direct_node_count"] == 2
    assert orders["subtree_node_count"] == 3

    assert first["coverage"] == {
        "graph": {
            "nodes": 5,
            "bound_edges": 5,
            "source_edges": 6,
            "unresolved_edges": 1,
        },
        "domains": {
            "assigned_nodes": 5,
            "unassigned_nodes": 0,
            "returned": 2,
            "total": 4,
            "complete": False,
        },
        "relations": {
            "returned": 2,
            "total_count": 6,
            "complete": False,
            "edge_count": 6,
            "bound_edge_count": 5,
            "unresolved_edge_count": 1,
        },
        "materialization": {
            "semantic_budget": {
                "truncated": False,
                "unresolved_edge_count": 0,
            }
        },
    }
    assert second["coverage"]["domains"]["complete"] is True
    assert second["metadata"]["next_cursor"] is None
    relation_items = list(first["facets"]["relations"]["items"])
    relation_cursor = first["facets"]["relations"]["next_cursor"]
    while relation_cursor is not None:
        relation_page = adapter.graph(
            parameters={
                "view": "inventory",
                "limit": 2,
                "cursor": relation_cursor,
            }
        )
        relation_items.extend(relation_page["facets"]["relations"]["items"])
        relation_cursor = relation_page["facets"]["relations"]["next_cursor"]
    assert {item["raw_type"]: item["edge_count"] for item in relation_items} == {
        "contains_domain": 1,
        "contains_symbol": 1,
        "cross_reference": 1,
        "depends_on": 1,
        "missing_target": 1,
        "semantic_declares": 1,
    }
    missing = next(
        item for item in relation_items if item["raw_type"] == "missing_target"
    )
    assert missing["bound_edge_count"] == 0
    assert missing["unresolved_edge_count"] == 1
    assert first["warnings"][0].startswith(
        "1 graph relation has an unavailable"
    )


def test_topology_domain_scope_controls_descendants_and_reports_boundary() -> None:
    payload = _scoped_graph_payload()
    adapter, projection = _graph_adapter(payload)
    sales_scope = _domain_scope_key(payload, path="sales")

    included = adapter.graph(
        parameters={
            "view": "topology",
            "domain_scope": sales_scope,
            "include_subdomains": True,
            "limit": 10,
            "max_edges": 10,
        }
    )
    included_call = projection.calls[-1]
    direct_only = adapter.graph(
        parameters={
            "view": "topology",
            "domain_scope": sales_scope,
            "include_subdomains": False,
            "limit": 10,
            "max_edges": 10,
        }
    )
    direct_call = projection.calls[-1]

    assert {
        node["id"] for node in included_call["nodes"]
    } == {
        "sales-root",
        "orders",
        "orders-api",
        "semantic:orders",
    }
    assert {
        edge["edge_id"] for edge in included_call["edges"]
    } == {
        "sales-orders",
        "orders-api",
        "cross-node-page",
        "semantic-orders",
    }
    assert included["metadata"]["content_graph_revision"].startswith("sha256:")
    assert included["metadata"] == {
        "knowledge_index_id": "index-1",
        "view": "topology",
        "content_graph_revision": included["metadata"]["content_graph_revision"],
        "next_cursor": None,
        "total_nodes": 4,
        "total_edges": 4,
        "source_edge_count": 6,
        "unresolved_edge_count": 1,
        "internal_edge_count": 4,
        "edge_capped": False,
        "max_edges": 10,
        "semantic_budget": {
            "truncated": False,
            "unresolved_edge_count": 0,
        },
        "domain_scope": sales_scope,
        "domain_scope_label": "sales",
        "include_subdomains": True,
        "global_total_nodes": 5,
        "global_total_edges": 5,
        "global_source_edge_count": 6,
        "global_unresolved_edge_count": 1,
        "scope_total_nodes": 4,
        "scope_boundary_edge_count": 1,
        "scope_unresolved_edge_count": 0,
        "remaining_nodes": 0,
        "window_node_limit": 10,
        "window_domain_group_count": 2,
        "scope_domain_group_count": 2,
        "delivery_complete": True,
    }
    assert any(
        "crosses the selected domain boundary" in item
        for item in included["warnings"]
    )

    assert [node["id"] for node in direct_call["nodes"]] == ["sales-root"]
    assert direct_call["edges"] == []
    assert direct_only["metadata"]["include_subdomains"] is False
    assert direct_only["metadata"]["scope_total_nodes"] == 1
    assert direct_only["metadata"]["scope_boundary_edge_count"] == 2
    assert direct_only["metadata"]["delivery_complete"] is True


def test_staged_graph_pages_deliver_all_nodes_and_edges_without_cross_page_loss() -> None:
    adapter, _projection = _graph_adapter(_scoped_graph_payload())

    delivered_nodes: list[dict] = []
    node_page_sizes: list[int] = []
    cursor = None
    while True:
        page = adapter.graph(
            parameters={
                "view": "staged",
                "stage": "nodes",
                "limit": 2,
                "max_edges": 2,
                "cursor": cursor,
            }
        )
        delivered_nodes.extend(page["nodes"])
        node_page_sizes.append(page["metadata"]["delivery_returned"])
        cursor = page["metadata"]["next_cursor"]
        if cursor is None:
            assert page["metadata"]["delivery_complete"] is True
            break

    delivered_edges: list[dict] = []
    edge_page_sizes: list[int] = []
    cursor = None
    while True:
        page = adapter.graph(
            parameters={
                "view": "staged",
                "stage": "edges",
                "limit": 2,
                "max_edges": 2,
                "cursor": cursor,
            }
        )
        delivered_edges.extend(page["edges"])
        edge_page_sizes.append(page["metadata"]["delivery_returned"])
        cursor = page["metadata"]["next_cursor"]
        if cursor is None:
            assert page["metadata"]["delivery_complete"] is True
            break

    assert node_page_sizes == [2, 2, 1]
    assert [node["id"] for node in delivered_nodes] == [
        "sales-root",
        "orders",
        "orders-api",
        "support",
        "semantic:orders",
    ]
    assert edge_page_sizes == [2, 2, 2]
    assert [edge["edge_id"] for edge in delivered_edges] == [
        "sales-orders",
        "orders-api",
        "domain-boundary",
        "cross-node-page",
        "dangling",
        "semantic-orders",
    ]
    assert any(edge["edge_id"] == "cross-node-page" for edge in delivered_edges)
    assert any(edge["edge_id"] == "semantic-orders" for edge in delivered_edges)
    assert any(edge["edge_id"] == "dangling" for edge in delivered_edges)
    assert len({node["id"] for node in delivered_nodes}) == 5
    assert len({edge["edge_id"] for edge in delivered_edges}) == 6


def test_staged_cursor_is_bound_to_scope_options() -> None:
    payload = _scoped_graph_payload()
    adapter, _projection = _graph_adapter(payload)
    sales_scope = _domain_scope_key(payload, path="sales")
    first = adapter.graph(
        parameters={
            "view": "staged",
            "stage": "nodes",
            "domain_scope": sales_scope,
            "include_subdomains": True,
            "limit": 1,
        }
    )

    with pytest.raises(SourceControlProductionAdapterError) as exc_info:
        adapter.graph(
            parameters={
                "view": "staged",
                "stage": "nodes",
                "domain_scope": sales_scope,
                "include_subdomains": False,
                "limit": 1,
                "cursor": first["metadata"]["next_cursor"],
            }
        )

    assert exc_info.value.reason_code == "graph_cursor_scope_mismatch"
    assert exc_info.value.status_code == 400


def test_staged_cursor_remains_valid_for_manifest_only_change() -> None:
    payload = _scoped_graph_payload()
    adapter, _projection = _graph_adapter(payload)
    first = adapter.graph(
        parameters={
            "view": "staged",
            "stage": "nodes",
            "limit": 1,
        }
    )
    payload["state"]["manifest_hash"] = "sha256:graph-revision-2"

    second = adapter.graph(
        parameters={
            "view": "staged",
            "stage": "nodes",
            "limit": 1,
            "cursor": first["metadata"]["next_cursor"],
        }
    )

    assert second["metadata"]["content_graph_revision"] == first["metadata"][
        "content_graph_revision"
    ]
    assert second["metadata"]["delivery_returned"] == 1


def test_legacy_graph_resolver_tracks_the_derived_metrics_sidecar(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "cc_graph_index.json"

    class _Resolver:
        def resolve(self, _index):
            return index_path

    class _Cache:
        def __init__(self) -> None:
            self.kwargs = {}

        def get(self, **kwargs):
            self.kwargs = kwargs
            return object()

    cache = _Cache()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._resolver = _Resolver()
    adapter._graph_store_cache = cache

    adapter._graph_store(object())

    assert cache.kwargs == {
        "index_path": index_path,
        "visual_metrics_path": tmp_path
        / "cc_graph_index.visual_metrics.json",
    }


def test_destination_catalog_requires_server_scope_and_model_evidence() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            AgentInfoDB(
                url="http://worker.test",
                name="worker-example",
                role="worker",
                status="online",
                registration_validated=True,
                runtime_targets=[
                    {
                        "runtime_id": "runtime-example",
                        "runtime_kind": "ollama",
                        "provider_id": "ollama",
                        "model_id": "code-model",
                        "model_class": "code",
                        "provider_location": "private_network",
                        "data_residency": "eu",
                        "source_access_authorized": True,
                        "tenant_id": "tenant-example",
                        "project_id": "project-example",
                    }
                ],
            )
        )
        db.commit()
    catalog = ScopedWorkerModelDestinationCatalog(
        engine=engine,
        model_supplier=lambda: (_Model(),),
    )

    allowed, _ = catalog.list(
        tenant_id="tenant-example",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )
    denied, _ = catalog.list(
        tenant_id="other-tenant",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )

    assert len(allowed) == 1
    assert allowed[0].worker_id == "worker-example"
    assert denied == ()
    assert (
        catalog.get(
            tenant_id="tenant-example",
            project_id="project-example",
            destination_id=allowed[0].destination_id,
        )
        == allowed[0]
    )


def test_destination_catalog_separates_execution_and_model_provider() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            AgentInfoDB(
                url="http://worker.test",
                name="worker-example",
                role="worker",
                status="online",
                registration_validated=True,
                runtime_targets=[
                    {
                        "runtime_id": "runtime-example",
                        "runtime_kind": "docker_container",
                        "provider_id": "codex",
                        "model_provider_id": "lmstudio",
                        "model_id": "qwen/qwen3.5-9b",
                        "model_class": "code",
                        "provider_location": "local_container",
                        "data_residency": "local",
                        "source_access_authorized": True,
                        "global_source_access": True,
                    }
                ],
            )
        )
        db.commit()
    catalog = ScopedWorkerModelDestinationCatalog(
        engine=engine,
        model_supplier=lambda: (
            _Model(
                provider_id="lmstudio",
                model_id="qwen/qwen3.5-9b",
            ),
        ),
    )

    destinations, _ = catalog.list(
        tenant_id="tenant-example",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )

    assert len(destinations) == 1
    assert destinations[0].provider_id == "codex"
    assert destinations[0].model_id == "qwen/qwen3.5-9b"


def test_effective_access_uses_scoped_destination_and_persistent_grant() -> None:
    engine = _engine()
    policy_digest = "9" * 64
    policy_snapshot_id = derive_policy_snapshot_id(
        tenant_id="tenant-example",
        project_id="project-example",
        policy_id="policy-example",
        version=1,
        policy_digest=policy_digest,
    )
    with Session(engine) as db:
        db.add(
            AgentInfoDB(
                url="http://worker.test",
                name="worker-example",
                role="worker",
                status="online",
                registration_validated=True,
                runtime_targets=[
                    {
                        "runtime_id": "runtime-example",
                        "runtime_kind": "ollama",
                        "provider_id": "ollama",
                        "model_id": "code-model",
                        "model_class": "code",
                        "provider_location": "private_network",
                        "data_residency": "eu",
                        "source_access_authorized": True,
                        "tenant_id": "tenant-example",
                        "project_id": "project-example",
                    }
                ],
            )
        )
        db.add(
            SourceConnectionDB(
                connection_id=f"conn_{'a' * 64}",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                connection_identity_digest="c" * 64,
                display_name="Example",
                sensitivity="internal",
                state="active",
                lock_version=1,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            SourceRevisionDB(
                source_revision_id=f"srev_{'b' * 64}",
                connection_id=f"conn_{'a' * 64}",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                sensitivity="internal",
                revision_token="main",
                revision_digest="d" * 64,
                content_manifest_id=f"manifest_{'e' * 64}",
                content_manifest_digest="f" * 64,
                admission_state="admitted",
                captured_at_epoch=1.0,
            )
        )
        db.add(
            ContextPolicyVersionDB(
                record_id=policy_snapshot_id,
                tenant_id="tenant-example",
                project_id="project-example",
                policy_id="policy-example",
                version=1,
                state="active",
                document_json={"policy_id": "policy-example", "version": 1},
                policy_digest=policy_digest,
                etag=policy_digest,
                created_by="owner-example",
                created_at="2026-01-01T00:00:00Z",
                updated_by="owner-example",
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        db.commit()
    destinations = ScopedWorkerModelDestinationCatalog(
        engine=engine,
        model_supplier=lambda: (_Model(),),
    )
    available, _ = destinations.list(
        tenant_id="tenant-example",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )
    with Session(engine) as db:
        db.add(
            SourceAccessGrantDB(
                grant_id=f"grant_{'1' * 64}",
                grant_family_id="grant-family-example",
                grant_version=1,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                source_revision_id=f"srev_{'b' * 64}",
                destination_id=available[0].destination_id,
                operation=GrantOperation.INDEX.value,
                transformation=GrantTransformation.REDACTED.value,
                purpose="code-review",
                policy_version=policy_snapshot_id,
                policy_snapshot_digest=policy_digest,
                state="active",
                issued_at_epoch=1.0,
                expires_at_epoch=4_102_444_800.0,
                lock_version=1,
                updated_at_epoch=1.0,
            )
        )
        db.commit()
    service = build_scoped_effective_access_service(
        engine=engine,
        destinations=destinations,
        tenant_id="tenant-example",
        project_id="project-example",
    )

    decision = service.preview(
        tenant_id="tenant-example",
        project_id="project-example",
        source_revision_id=f"srev_{'b' * 64}",
        destination_id=available[0].destination_id,
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="code-review",
    )

    assert decision.decision == "allow"
    assert decision.reason_codes == ("active_grant",)


def test_bound_index_submission_keeps_scope_and_access_intent_hub_owned() -> None:
    class Planner:
        def plan_bound_source_revision(self, **kwargs):
            assert set(kwargs) == {
                "tenant_id",
                "project_id",
                "actor_id",
                "connection_id",
                "source_revision_id",
                "source_revision_digest",
                "content_manifest_digest",
                "descriptor",
                "idempotency_key",
            }
            return {
                "hub_task_id": "hub-task-example",
                "source_revision_id": kwargs["source_revision_id"],
                "source_revision_digest": kwargs[
                    "source_revision_digest"
                ],
                "admission_digest": "1" * 64,
                "policy_snapshot_id": "policy-example-v1",
                "policy_snapshot_digest": "2" * 64,
                "destination_id": f"dest_{'3' * 64}",
                "destination_digest": "4" * 64,
                "source_access_grant_id": f"grant_{'5' * 64}",
                "source_access_grant_digest": "6" * 64,
                "files": [],
                "resource_budget": {},
                "assignment": {},
                "destination_selection": {},
                "source_scope": "github",
                "source_id": "source-example",
                "source_payload_digest": "7" * 64,
                "source_payload_connection_id": kwargs["connection_id"],
                "records": [],
            }

    class Jobs:
        def __init__(self) -> None:
            self.arguments = None

        def submit_bound_source_revision_job(self, **kwargs):
            self.arguments = kwargs
            return {"job_id": "job-example", "status": "todo"}

    jobs = Jobs()
    adapter = HubBoundSourceIndexSubmissionAdapter(
        planner=Planner(),
        job_service=jobs,
    )
    connection = SourceConnectionDB(
        connection_id=f"conn_{'a' * 64}",
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        connector_type="github",
        connection_identity_digest="c" * 64,
        display_name="Example",
        sensitivity="internal",
        state="active",
        lock_version=1,
        created_at_epoch=1.0,
        updated_at_epoch=1.0,
    )
    revision = SourceRevisionDB(
        source_revision_id=f"srev_{'b' * 64}",
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        connector_type=connection.connector_type,
        sensitivity=connection.sensitivity,
        revision_token="main",
        revision_digest="d" * 64,
        content_manifest_id=f"manifest_{'e' * 64}",
        content_manifest_digest="f" * 64,
        admission_state="admitted",
        captured_at_epoch=1.0,
    )

    result = adapter.submit(
        connection=connection,
        revision=revision,
        descriptor={"source_id": "source-example"},
        actor_id="admin-example",
        idempotency_key="idempotency-example",
        profile_name="code-profile",
    )

    assert result == {"job_id": "job-example", "status": "todo"}
    assert jobs.arguments["tenant_id"] == "tenant-example"
    assert jobs.arguments["project_id"] == "project-example"
    assert "source_payload_digest" not in jobs.arguments
    assert "source_payload_connection_id" not in jobs.arguments
    assert jobs.arguments["owner_id"] == "owner-example"
    assert jobs.arguments["created_by"] == "admin-example"
    assert jobs.arguments["source_operation"] == "index"
    assert jobs.arguments["source_transformation"] == "redacted"
    assert jobs.arguments["source_purpose"] == "knowledge-index"
    assert jobs.arguments["source_scope"] == "repo_path"


def _seed_deletable_index(engine, output_dir, manifest_digest) -> None:
    connection_id = f"conn_{'a' * 64}"
    revision_id = f"srev_{'b' * 64}"
    with Session(engine) as db:
        db.add(
            SourceConnectionDB(
                connection_id=connection_id,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                connection_identity_digest="c" * 64,
                display_name="Example",
                sensitivity="internal",
                state="disabled",
                lock_version=1,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            SourceRevisionDB(
                source_revision_id=revision_id,
                connection_id=connection_id,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                sensitivity="internal",
                revision_token="main",
                revision_digest="d" * 64,
                content_manifest_id=f"manifest_{'e' * 64}",
                content_manifest_digest="f" * 64,
                admission_state="admitted",
                captured_at_epoch=1.0,
            )
        )
        db.add(
            KnowledgeIndexSourceBindingDB(
                knowledge_index_id="index-example",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connection_id=connection_id,
                source_revision_id=revision_id,
                policy_snapshot_id="policy-example",
                policy_snapshot_digest="1" * 64,
                index_contract_version="v1",
                status="tombstoned",
                artifact_manifest_digest=manifest_digest,
                lock_version=2,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            KnowledgeIndexRunSourceBindingDB(
                index_run_id="run-example",
                knowledge_index_id="index-example",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                source_revision_id=revision_id,
                policy_snapshot_id="policy-example",
                policy_snapshot_digest="1" * 64,
                status="completed",
                artifact_manifest_digest=manifest_digest,
                artifacts_verified=True,
                lock_version=1,
                created_at_epoch=1.0,
                completed_at_epoch=2.0,
            )
        )
        db.add(
            KnowledgeIndexDB(
                id="index-example",
                latest_run_id="run-example",
                source_scope="github",
                status="completed",
                output_dir=str(output_dir),
                manifest_path=str(output_dir / "manifest.json"),
                index_metadata={
                    "retention_released": True,
                    "retention_approval_id": "approval-example",
                },
                created_by="owner-example",
            )
        )
        db.add(
            KnowledgeIndexRunDB(
                id="run-example",
                knowledge_index_id="index-example",
                status="completed",
                output_dir=str(output_dir),
                manifest_path=str(output_dir / "manifest.json"),
            )
        )
        db.commit()


def test_artifact_deletion_is_contained_audited_and_replay_safe(
    tmp_path,
) -> None:
    engine = _engine()
    root = tmp_path / "knowledge_indices"
    output = root / "github" / "index-example" / "run-example"
    output.mkdir(parents=True)
    manifest = output / "manifest.json"
    manifest.write_text('{"schema":"test-manifest.v1"}', encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    _seed_deletable_index(engine, output, digest)
    service = ContainedArtifactDeletionService(
        engine=engine,
        artifact_root=root,
    )
    principal = SourceControlPrincipal(
        subject_id="admin-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"admin"}),
    )

    first = service.delete(
        principal=principal,
        knowledge_index_id="index-example",
        expected_version=2,
        approval_id="approval-example",
    )
    replay = service.delete(
        principal=principal,
        knowledge_index_id="index-example",
        expected_version=2,
        approval_id="approval-example",
    )

    assert first == replay
    assert not output.exists()
    assert service.is_deleted(knowledge_index_id="index-example")


def test_artifact_deletion_rejects_path_outside_allowed_root(
    tmp_path,
) -> None:
    engine = _engine()
    root = tmp_path / "knowledge_indices"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = outside / "manifest.json"
    manifest.write_text('{"schema":"test-manifest.v1"}', encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    _seed_deletable_index(engine, outside, digest)
    service = ContainedArtifactDeletionService(
        engine=engine,
        artifact_root=root,
    )

    with pytest.raises(
        SourceControlProductionAdapterError,
        match="artifact_output_outside_root",
    ):
        service.delete(
            principal=SourceControlPrincipal(
                subject_id="admin-example",
                tenant_id="tenant-example",
                project_id="project-example",
                roles=frozenset({"admin"}),
            ),
            knowledge_index_id="index-example",
            expected_version=2,
            approval_id="approval-example",
        )
