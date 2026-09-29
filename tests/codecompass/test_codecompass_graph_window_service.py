from __future__ import annotations

from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowService,
)


def _node(node_id: str) -> dict[str, str]:
    return {"id": node_id}


def _edge(edge_id: str, source: str, target: str) -> dict[str, str]:
    return {
        "edge_id": edge_id,
        "source_id": source,
        "target_id": target,
    }


def test_select_prefers_a_connected_topology_prefix_over_store_order() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [
        _node("isolated"),
        _node("leaf-b"),
        _node("hub"),
        _node("leaf-a"),
        _node("tail"),
    ]
    edges = [
        _edge("hub-a", "hub", "leaf-a"),
        _edge("hub-b", "hub", "leaf-b"),
        _edge("a-tail", "leaf-a", "tail"),
    ]

    window = service.select(
        nodes=nodes,
        edges=edges,
        node_limit=3,
        edge_limit=10,
    )

    assert [node["id"] for node in window.nodes] == [
        "hub",
        "leaf-a",
        "leaf-b",
    ]
    assert [edge["edge_id"] for edge in window.edges] == [
        "hub-a",
        "hub-b",
    ]
    assert window.total_node_count == 5
    assert window.total_edge_count == 3
    assert window.source_edge_count == 3
    assert window.unresolved_edge_count == 0
    assert window.internal_edge_count == 2
    assert window.edge_capped is False


def test_edge_cap_prioritizes_backbone_within_an_activation_rank() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [_node(node_id) for node_id in ("a", "b", "c", "d")]
    edges = [
        _edge("ab", "a", "b"),
        _edge("da", "d", "a"),
        _edge("db-cycle", "d", "b"),
        _edge("dc", "d", "c"),
    ]

    window = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=[["a"], ["b"], ["c"], ["d"]],
        node_limit=4,
        edge_limit=3,
    )

    assert [edge["edge_id"] for edge in window.edges] == ["ab", "da", "dc"]
    assert window.internal_edge_count == 4
    assert window.edge_capped is True


def test_growing_window_and_edge_cap_keep_existing_parallel_edges() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [_node(node_id) for node_id in ("a", "b", "c")]
    edges = [
        _edge("ab", "a", "b"),
        _edge("ab-parallel", "a", "b"),
        _edge("bc", "b", "c"),
    ]
    groups = [["a"], ["b"], ["c"]]

    smaller = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=groups,
        node_limit=2,
        edge_limit=2,
    )
    larger = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=groups,
        node_limit=3,
        edge_limit=3,
    )
    smaller_ids = [edge["edge_id"] for edge in smaller.edges]
    larger_ids = [edge["edge_id"] for edge in larger.edges]

    assert smaller_ids == ["ab", "ab-parallel"]
    assert larger_ids == ["ab", "ab-parallel", "bc"]
    assert smaller_ids == larger_ids[: len(smaller_ids)]


def test_select_drops_duplicate_nodes_and_dangling_edges_deterministically() -> None:
    service = CodeCompassGraphWindowService()

    first = service.select(
        nodes=[_node("a"), _node("a"), object(), _node("b")],
        edges=[_edge("ab", "a", "b"), _edge("missing", "a", "missing")],
        node_limit=10,
        edge_limit=10,
    )
    second = service.select(
        nodes=[_node("a"), _node("a"), object(), _node("b")],
        edges=[_edge("ab", "a", "b"), _edge("missing", "a", "missing")],
        node_limit=10,
        edge_limit=10,
    )

    assert [node["id"] for node in first.nodes] == ["a", "b"]
    assert [edge["edge_id"] for edge in first.edges] == ["ab"]
    assert first == second
    assert first.total_node_count == 2
    assert first.total_edge_count == 1
    assert first.source_edge_count == 2
    assert first.unresolved_edge_count == 1


def test_select_orders_many_sparse_nodes_without_repeated_component_scans() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [_node(f"node-{index:05d}") for index in range(10_000)]

    window = service.select(
        nodes=nodes,
        edges=[],
        node_limit=500,
        edge_limit=10,
    )

    assert [node["id"] for node in window.nodes[:3]] == [
        "node-00000",
        "node-00001",
        "node-00002",
    ]
    assert len(window.nodes) == 500
    assert window.total_node_count == 10_000
    assert window.edges == ()


def test_select_represents_every_group_before_adding_more_nodes() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [
        _node("large-hub"),
        *[_node(f"large-{index}") for index in range(6)],
        _node("small-only"),
    ]
    edges = [
        _edge(f"large-edge-{index}", "large-hub", f"large-{index}")
        for index in range(6)
    ]

    window = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=[
            ["large-hub", *[f"large-{index}" for index in range(6)]],
            ["small-only"],
        ],
        node_limit=2,
        edge_limit=10,
    )

    assert [node["id"] for node in window.nodes] == [
        "large-hub",
        "small-only",
    ]
    assert window.represented_group_count == 2
    assert window.total_group_count == 2


def test_group_balanced_strategy_windows_are_nested_and_cover_55_domains() -> None:
    service = CodeCompassGraphWindowService()
    groups = [
        [f"domain-{domain:02d}-node-{node:02d}" for node in range(12)]
        for domain in range(55)
    ]
    nodes = [_node(node_id) for group in groups for node_id in group]
    edges = [
        _edge(
            f"domain-{domain:02d}-edge-{node:02d}",
            group[0],
            group[node],
        )
        for domain, group in enumerate(groups)
        for node in range(1, len(group))
    ]

    fast = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=groups,
        node_limit=100,
        edge_limit=400,
    )
    balanced = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=groups,
        node_limit=250,
        edge_limit=1_000,
    )
    broad = service.select(
        nodes=nodes,
        edges=edges,
        node_groups=groups,
        node_limit=500,
        edge_limit=2_000,
    )
    fast_ids = [node["id"] for node in fast.nodes]
    balanced_ids = [node["id"] for node in balanced.nodes]
    broad_ids = [node["id"] for node in broad.nodes]

    assert fast_ids == balanced_ids[:100]
    assert balanced_ids == broad_ids[:250]
    assert len(broad_ids) == 500
    assert fast.represented_group_count == 55
    assert fast.total_group_count == 55
    assert {node_id.split("-node-")[0] for node_id in fast_ids} == {
        f"domain-{domain:02d}" for domain in range(55)
    }
