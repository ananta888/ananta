from __future__ import annotations

import pytest

from agent.services.codecompass_graph_domain_catalog_service import (
    _MAX_DOMAIN_EXPANDED_CHARACTERS,
    _MAX_DOMAIN_HIERARCHY_DEPTH,
    _MAX_DOMAIN_SEGMENT_CHARACTERS,
    _MAX_DOMAIN_TEXT_CHARACTERS,
    CodeCompassGraphDomainCatalog,
    CodeCompassGraphDomainCatalogService,
    CodeCompassGraphDomainFacet,
)


def _facet(
    catalog: CodeCompassGraphDomainCatalog,
    *,
    source: str,
    path: str,
) -> CodeCompassGraphDomainFacet:
    return next(facet for facet in catalog.facets if facet.source == source and facet.path == path)


def test_catalog_builds_hierarchy_with_explicit_domain_priority() -> None:
    service = CodeCompassGraphDomainCatalogService()

    catalog = service.catalog(
        nodes=[
            {
                "id": "explicit",
                "domain_path": "ignored/declared",
                "file": "ignored/fallback.py",
                "attributes": {"domain_id": "commerce.orders"},
            },
            {
                "id": "declared",
                "domain_path": "platform/runtime",
                "file": "ignored/declared.py",
            },
            {
                "id": "path-only",
                "kind": "source_file",
                "file": "src/payments/api.py",
            },
        ]
    )

    assert {(facet.source, facet.path) for facet in catalog.facets} == {
        ("domain_id", "commerce"),
        ("domain_id", "commerce.orders"),
        ("domain_path", "platform"),
        ("domain_path", "platform/runtime"),
        ("path", "src"),
        ("path", "src/payments"),
    }
    commerce = _facet(catalog, source="domain_id", path="commerce")
    orders = _facet(catalog, source="domain_id", path="commerce.orders")
    assert commerce.parent_key is None
    assert commerce.depth == 0
    assert commerce.has_children is True
    assert orders.parent_key == commerce.key
    assert orders.depth == 1


def test_catalog_reports_direct_and_subtree_counts() -> None:
    service = CodeCompassGraphDomainCatalogService()

    catalog = service.catalog(
        nodes=[
            {"id": "sales-root", "domain_id": "sales"},
            {"id": "orders-one", "domain_id": "sales.orders"},
            {"id": "orders-two", "domain_id": "sales.orders"},
            {"id": "invoice", "domain_id": "sales.billing.invoices"},
        ]
    )

    sales = _facet(catalog, source="domain_id", path="sales")
    orders = _facet(catalog, source="domain_id", path="sales.orders")
    billing = _facet(catalog, source="domain_id", path="sales.billing")
    invoices = _facet(
        catalog,
        source="domain_id",
        path="sales.billing.invoices",
    )
    assert (sales.direct_node_count, sales.subtree_node_count) == (1, 4)
    assert (orders.direct_node_count, orders.subtree_node_count) == (2, 2)
    assert (billing.direct_node_count, billing.subtree_node_count) == (0, 1)
    assert (invoices.direct_node_count, invoices.subtree_node_count) == (1, 1)
    assert billing.has_children is True
    assert invoices.has_children is False


def test_select_can_include_or_exclude_descendant_domains() -> None:
    service = CodeCompassGraphDomainCatalogService()
    nodes = [
        {"id": "sales-root", "domain_id": "sales"},
        {"id": "orders", "domain_id": "sales.orders"},
        {"id": "orders-api", "domain_id": "sales.orders.api"},
        {"id": "support", "domain_id": "support"},
    ]
    catalog = service.catalog(nodes=nodes)
    sales_key = _facet(catalog, source="domain_id", path="sales").key
    orders_key = _facet(catalog, source="domain_id", path="sales.orders").key

    sales_tree = service.select(
        nodes=nodes,
        scope_key=sales_key,
        include_descendants=True,
    )
    sales_direct = service.select(
        nodes=nodes,
        scope_key=sales_key,
        include_descendants=False,
    )
    orders_tree = service.select(
        nodes=nodes,
        scope_key=orders_key,
        include_descendants=True,
    )
    orders_direct = service.select(
        nodes=nodes,
        scope_key=orders_key,
        include_descendants=False,
    )

    assert [node["id"] for node in sales_tree.nodes] == [
        "sales-root",
        "orders",
        "orders-api",
    ]
    assert [node["id"] for node in sales_direct.nodes] == ["sales-root"]
    assert [node["id"] for node in orders_tree.nodes] == [
        "orders",
        "orders-api",
    ]
    assert [node["id"] for node in orders_direct.nodes] == ["orders"]
    assert sales_tree.global_node_count == len(nodes)


def test_catalog_and_selection_keep_unassigned_nodes_visible() -> None:
    service = CodeCompassGraphDomainCatalogService()
    nodes = [
        {"id": "missing-domain"},
        {"id": "root-file", "kind": "source_file", "file": "README.md"},
        {"id": "docs", "kind": "directory", "path": "docs"},
    ]

    catalog = service.catalog(nodes=nodes)
    unassigned = _facet(catalog, source="unassigned", path="unassigned")
    selection = service.select(
        nodes=nodes,
        scope_key=unassigned.key,
        include_descendants=True,
    )

    assert catalog.total_node_count == 3
    assert catalog.assigned_node_count == 1
    assert catalog.unassigned_node_count == 2
    assert unassigned.label == "Nicht zugeordnet"
    assert (unassigned.direct_node_count, unassigned.subtree_node_count) == (2, 2)
    assert [node["id"] for node in selection.nodes] == [
        "missing-domain",
        "root-file",
    ]


def test_select_rejects_an_unknown_scope() -> None:
    service = CodeCompassGraphDomainCatalogService()

    with pytest.raises(ValueError, match="^graph_domain_scope_unknown$"):
        service.select(
            nodes=[{"id": "known", "domain_id": "known"}],
            scope_key="domain_id_dW5rbm93bg",
            include_descendants=True,
        )


def test_catalog_does_not_expose_absolute_paths_as_domain_facets() -> None:
    service = CodeCompassGraphDomainCatalogService()

    catalog = service.catalog(
        nodes=[
            {
                "id": "absolute",
                "kind": "source_file",
                "file": "/srv/private/repository/agent/api.py",
            }
        ]
    )

    assert [(facet.source, facet.path) for facet in catalog.facets] == [("unassigned", "unassigned")]


def test_catalog_deduplicates_nodes_deterministically_by_identifier() -> None:
    service = CodeCompassGraphDomainCatalogService()
    preferred = {"id": "duplicate", "domain_id": "zeta"}
    duplicate = {"node_id": "duplicate", "domain_id": "ignored"}
    alpha = {"node_id": "unique", "domain_id": "alpha"}

    first = service.catalog(nodes=[preferred, duplicate, alpha, "not-a-node", {"domain_id": "no-id"}])
    reordered = service.catalog(nodes=[alpha, preferred, duplicate])

    assert first.total_node_count == 2
    assert [facet.to_wire() for facet in first.facets] == [facet.to_wire() for facet in reordered.facets]
    assert [(facet.source, facet.path) for facet in first.facets] == [
        ("domain_id", "alpha"),
        ("domain_id", "zeta"),
    ]
    zeta_key = _facet(first, source="domain_id", path="zeta").key
    selection = service.select(
        nodes=[preferred, duplicate, alpha],
        scope_key=zeta_key,
        include_descendants=True,
    )
    assert selection.nodes == (preferred,)
    assert selection.nodes[0] is preferred


def test_catalog_accepts_domain_hierarchy_at_the_depth_limit() -> None:
    service = CodeCompassGraphDomainCatalogService()
    domain_id = ".".join("d" for _ in range(_MAX_DOMAIN_HIERARCHY_DEPTH))
    node = {"id": "bounded", "domain_id": domain_id}

    index = service.prepare(nodes=[node])

    assert len(index.domain_catalog.facets) == _MAX_DOMAIN_HIERARCHY_DEPTH
    assert index.domain_catalog.assigned_node_count == 1
    leaf = index.domain_catalog.facets[-1]
    assert leaf.path == domain_id
    assert leaf.depth == _MAX_DOMAIN_HIERARCHY_DEPTH - 1
    assert index.select(scope_key=leaf.key, include_descendants=False).nodes == (node,)


def test_catalog_keeps_oversized_domain_nodes_visible_in_fallback_scope() -> None:
    service = CodeCompassGraphDomainCatalogService()
    oversized_text = "x" * (_MAX_DOMAIN_TEXT_CHARACTERS + 1)
    oversized_segment = "x" * (_MAX_DOMAIN_SEGMENT_CHARACTERS + 1)
    excessive_depth = ".".join("d" for _ in range(_MAX_DOMAIN_HIERARCHY_DEPTH + 1))
    excessive_path_depth = "/".join(["directory"] * (_MAX_DOMAIN_HIERARCHY_DEPTH + 1) + ["module.py"])
    nodes = [
        {
            "id": "text",
            "domain_id": oversized_text,
            "file": "valid/fallback.py",
        },
        {"id": "segment", "domain_path": f"valid/{oversized_segment}"},
        {"id": "explicit-depth", "domain_id": excessive_depth},
        {"id": "path-depth", "kind": "source_file", "file": excessive_path_depth},
    ]

    index = service.prepare(nodes=nodes)

    assert [(facet.source, facet.path) for facet in index.domain_catalog.facets] == [("unassigned", "unassigned")]
    assert index.domain_catalog.total_node_count == len(nodes)
    assert index.domain_catalog.assigned_node_count == 0
    assert index.domain_catalog.unassigned_node_count == len(nodes)
    fallback = index.domain_catalog.facets[0]
    assert index.select(scope_key=fallback.key, include_descendants=True).nodes == tuple(nodes)


def test_catalog_caps_cumulative_hierarchy_prefix_storage() -> None:
    service = CodeCompassGraphDomainCatalogService()
    # The raw text, every segment, and the depth are individually valid.  Its
    # cumulative hierarchy prefixes exceed the separate expansion budget.
    segment_length = min(
        _MAX_DOMAIN_SEGMENT_CHARACTERS,
        (_MAX_DOMAIN_TEXT_CHARACTERS + 1) // _MAX_DOMAIN_HIERARCHY_DEPTH - 1,
    )
    domain_id = ".".join("x" * segment_length for _ in range(_MAX_DOMAIN_HIERARCHY_DEPTH))
    cumulative_prefix_characters = sum(
        len(".".join(domain_id.split(".")[:depth])) for depth in range(1, _MAX_DOMAIN_HIERARCHY_DEPTH + 1)
    )
    assert len(domain_id) <= _MAX_DOMAIN_TEXT_CHARACTERS
    assert cumulative_prefix_characters > _MAX_DOMAIN_EXPANDED_CHARACTERS

    catalog = service.catalog(nodes=[{"id": "expanded", "domain_id": domain_id}])

    assert [(facet.source, facet.path) for facet in catalog.facets] == [("unassigned", "unassigned")]
    assert catalog.unassigned_node_count == 1
