from __future__ import annotations

from agent.visual_process.models import VisualProcessEdge, VisualProcessStep
from agent.visual_process.presets import get_preset
from agent.visual_process.validator import VisualProcessValidator


def test_builder_critic_gauntlet_uses_catalog_roles_and_standard_graph_types() -> None:
    graph = get_preset("preset-builder-critic-gauntlet")

    assert graph is not None
    assert [(step.id, step.label, step.kind, step.role) for step in graph.steps] == [
        ("gauntlet-lead", "Lead", "plan_only", "lead"),
        ("gauntlet-builder", "Builder", "patch_propose", "developer"),
        ("gauntlet-critic", "Critic", "review", "critic"),
    ]
    assert len({step.id for step in graph.steps}) == 3
    assert all(type(step) is VisualProcessStep for step in graph.steps)
    assert all(type(edge) is VisualProcessEdge for edge in graph.edges)
    assert graph.step_by_id("gauntlet-critic").policy_hints == ["read_only"]


def test_builder_critic_gauntlet_fanout_and_feedback_snapshot() -> None:
    graph = get_preset("preset-builder-critic-gauntlet")

    assert graph is not None
    assert [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "condition": edge.condition.kind,
            "loop_kind": (
                edge.condition.loop_policy.kind
                if edge.condition.loop_policy is not None
                else None
            ),
            "max_iterations": (
                edge.condition.loop_policy.max_iterations
                if edge.condition.loop_policy is not None
                else None
            ),
        }
        for edge in graph.edges
    ] == [
        {
            "id": "gauntlet-lead-builder",
            "source": "gauntlet-lead",
            "target": "gauntlet-builder",
            "condition": "always",
            "loop_kind": None,
            "max_iterations": None,
        },
        {
            "id": "gauntlet-lead-critic",
            "source": "gauntlet-lead",
            "target": "gauntlet-critic",
            "condition": "always",
            "loop_kind": None,
            "max_iterations": None,
        },
        {
            "id": "gauntlet-builder-critic",
            "source": "gauntlet-builder",
            "target": "gauntlet-critic",
            "condition": "on_success",
            "loop_kind": None,
            "max_iterations": None,
        },
        {
            "id": "gauntlet-critic-builder-feedback",
            "source": "gauntlet-critic",
            "target": "gauntlet-builder",
            "condition": "back_edge",
            "loop_kind": "fixed",
            "max_iterations": 3,
        },
    ]


def test_builder_critic_gauntlet_declares_slot_without_resource_ids() -> None:
    graph = get_preset("preset-builder-critic-gauntlet")

    assert graph is not None
    marker = graph.metadata["ananta.caseflow.agent-preset"]
    assert marker == {
        "schema": "ananta.caseflow.agent-preset/v1",
        "binding_slots": [
            {
                "slot": "critic_benchmark_context",
                "step_id": "gauntlet-critic",
                "resource_type": "context_source",
                "required": True,
                "access": "read_only",
            }
        ],
    }
    serialized = graph.model_dump()
    critic = next(step for step in serialized["steps"] if step["id"] == "gauntlet-critic")
    assert "ananta.caseflow.agent-bindings" not in critic["metadata"]
    assert "resource_id" not in str(marker)
    assert "runtime_overlay" not in serialized
    assert all(step["run_state"] is None for step in serialized["steps"])


def test_builder_critic_gauntlet_is_a_valid_visual_process() -> None:
    graph = get_preset("preset-builder-critic-gauntlet")

    assert graph is not None
    result = VisualProcessValidator().validate(graph)
    assert result.valid, [issue.as_dict() for issue in result.errors()]
