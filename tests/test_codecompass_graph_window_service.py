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


def test_edge_cap_prioritizes_a_spanning_forest() -> None:
    service = CodeCompassGraphWindowService()
    nodes = [_node(node_id) for node_id in ("a", "b", "c", "d")]
    edges = [
        _edge("ab", "a", "b"),
        _edge("ab-parallel", "a", "b"),
        _edge("cd", "c", "d"),
        _edge("bc", "b", "c"),
    ]

    window = service.select(
        nodes=nodes,
        edges=edges,
        node_limit=4,
        edge_limit=3,
    )

    assert [edge["edge_id"] for edge in window.edges] == ["ab", "cd", "bc"]
    assert window.internal_edge_count == 4
    assert window.edge_capped is True


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
