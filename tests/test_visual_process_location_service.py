from __future__ import annotations

import time

import pytest

from agent.services.visual_process_location_service import VisualProcessLocationService
from agent.visual_process.models import (
    LoopPolicy,
    TransitionCondition,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)


def _graph(
    step_ids: list[str],
    edges: list[VisualProcessEdge],
    *,
    gates: set[str] = frozenset(),
) -> VisualProcessGraph:
    return VisualProcessGraph(
        id="graph-location",
        name="Location",
        definition_revision=2,
        steps=[
            VisualProcessStep(id=step_id, label=step_id, kind="analysis", gate=step_id in gates) for step_id in step_ids
        ],
        edges=edges,
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    kind: str = "always",
) -> VisualProcessEdge:
    condition = (
        TransitionCondition(kind="back_edge", loop_policy=LoopPolicy(kind="fixed", max_iterations=2))
        if kind == "back_edge"
        else TransitionCondition(
            kind=kind,
            output_name="result" if kind == "on_output" else None,
            expression="ok" if kind == "expression" else None,
        )
    )
    return VisualProcessEdge(id=edge_id, source=source, target=target, condition=condition)


CASES = [
    ("linear-start", _graph(["a", "b"], [_edge("e1", "a", "b")]), "node", "a", ("is_start", True)),
    ("linear-end", _graph(["a", "b"], [_edge("e1", "a", "b")]), "node", "b", ("dead_end", True)),
    ("branch", _graph(["a", "b", "c"], [_edge("e1", "a", "b"), _edge("e2", "a", "c")]), "node", "a", ("branch", True)),
    ("merge", _graph(["a", "b", "c"], [_edge("e1", "a", "c"), _edge("e2", "b", "c")]), "node", "c", ("merge", True)),
    ("disconnected", _graph(["a", "b", "x"], [_edge("e1", "a", "b")]), "node", "x", ("is_start", True)),
    ("multiple-starts", _graph(["a", "b", "c"], [_edge("e1", "a", "c")]), "canvas", None, ("start_count", 2)),
    (
        "back-edge-loop",
        _graph(["a", "b"], [_edge("e1", "a", "b"), _edge("e2", "b", "a", "back_edge")]),
        "node",
        "a",
        ("loop.member", True),
    ),
    ("self-loop", _graph(["a"], [_edge("e1", "a", "a", "back_edge")]), "node", "a", ("loop.self_loop", True)),
    ("failure-edge", _graph(["a", "b"], [_edge("e1", "a", "b", "on_failure")]), "edge", "e1", ("error_path", True)),
    ("gate", _graph(["a"], [], gates={"a"}), "node", "a", ("gate", True)),
    ("missing-node", _graph(["a"], []), "node", "missing", ("entity_exists", False)),
    ("normal-edge", _graph(["a", "b"], [_edge("e1", "a", "b")]), "edge", "e1", ("endpoint_integrity", True)),
    ("missing-edge", _graph(["a"], []), "edge", "missing", ("entity_exists", False)),
    ("dangling-edge", _graph(["a"], [_edge("e1", "a", "missing")]), "edge", "e1", ("endpoint_integrity", False)),
    (
        "forward-cycle",
        _graph(["a", "b"], [_edge("e1", "a", "b"), _edge("e2", "b", "a")]),
        "node",
        "b",
        ("loop.member", True),
    ),
    (
        "parallel-edges",
        _graph(["a", "b"], [_edge("e1", "a", "b"), _edge("e2", "a", "b")]),
        "node",
        "a",
        ("branch", False),
    ),
    (
        "output-edge",
        _graph(["a", "b"], [_edge("e1", "a", "b", "on_output")]),
        "edge",
        "e1",
        ("endpoint_integrity", True),
    ),
    (
        "expression-edge",
        _graph(["a", "b"], [_edge("e1", "a", "b", "expression")]),
        "edge",
        "e1",
        ("endpoint_integrity", True),
    ),
    ("runtime-target", _graph(["a"], []), "runtime", "a", ("reachable", True)),
    ("validation-target", _graph(["a"], []), "validation", "a", ("reachable", True)),
]


def _value(payload: dict, dotted: str):
    current = payload
    for token in dotted.split("."):
        current = current[token]
    return current


@pytest.mark.parametrize("name,graph,target_kind,entity_id,expected", CASES, ids=[item[0] for item in CASES])
def test_twenty_deterministic_topology_fixtures(name, graph, target_kind, entity_id, expected) -> None:
    del name
    result = (
        VisualProcessLocationService()
        .analyze(
            graph=graph,
            location={
                "target_kind": target_kind,
                "graph_id": graph.id,
                "entity_id": entity_id,
            },
        )
        .as_dict()
    )
    assert _value(result["focused_facts"], expected[0]) == expected[1]
    assert (
        result
        == VisualProcessLocationService()
        .analyze(
            graph=graph,
            location=result["location"],
        )
        .as_dict()
    )


def test_reference_size_graph_is_analyzed_under_budget() -> None:
    steps = [VisualProcessStep(id=f"s-{index:04d}", label=str(index), kind="analysis") for index in range(1000)]
    edges = [_edge(f"linear-{index:04d}", f"s-{index:04d}", f"s-{index + 1:04d}") for index in range(999)]
    edges.extend(
        _edge(
            f"jump-{index:04d}",
            f"s-{index % 998:04d}",
            f"s-{index % 998 + 2:04d}",
        )
        for index in range(1001)
    )
    graph = VisualProcessGraph(
        id="large",
        name="Large",
        base_graph_hash="a" * 64,
        steps=steps,
        edges=edges,
    )
    started = time.perf_counter()
    result = VisualProcessLocationService().analyze(
        graph=graph,
        location={"target_kind": "node", "graph_id": "large", "entity_id": "s-0500"},
    )
    elapsed = time.perf_counter() - started
    assert result.payload["graph_facts"]["step_count"] == 1000
    assert result.payload["graph_facts"]["edge_count"] == 2000
    assert elapsed <= 0.25
