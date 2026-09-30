from __future__ import annotations

from pathlib import Path

import pytest

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
    HubSourceControlOperationsAdapter,
    SourceControlProductionAdapterError,
)


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


def test_query_authorizes_only_the_hub_resolved_active_index() -> None:
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
    # Inject the retrieval double through the adapter's explicit
    # ``retrieval_factory`` seam (the constructor default is the production
    # KnowledgeIndexRetrievalService).
    adapter._retrieval_factory = _Retrieval

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
