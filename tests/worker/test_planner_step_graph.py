from __future__ import annotations

import pytest

from worker.planning.step_graph import assert_acyclic, build_step_graph, ready_steps


def test_step_graph_resolves_ready_steps_from_dependencies() -> None:
    graph = build_step_graph(
        steps=[
            {"step_id": "s1", "state": "done"},
            {"step_id": "s2", "depends_on": ["s1"], "state": "draft"},
            {"step_id": "s3", "depends_on": ["s2"], "state": "draft"},
        ]
    )
    assert_acyclic(graph)
    assert ready_steps(graph=graph) == ["s2"]


def test_step_graph_cycle_detection_rejects_invalid_plan() -> None:
    graph = build_step_graph(steps=[{"step_id": "a", "depends_on": ["b"]}, {"step_id": "b", "depends_on": ["a"]}])
    with pytest.raises(ValueError, match="step_graph_cycle_detected"):
        assert_acyclic(graph)

