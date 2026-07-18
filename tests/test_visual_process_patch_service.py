from __future__ import annotations

import pytest

from agent.services.visual_process_patch_service import (
    VisualProcessPatchRejected,
    VisualProcessPatchService,
)
from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph, VisualProcessStep
from ananta_contracts.visual_process_assistant import WorkflowPatch


def _graph() -> VisualProcessGraph:
    graph = VisualProcessGraph(
        id="g",
        name="Graph",
        definition_revision=4,
        steps=[
            VisualProcessStep(id="a", label="A", kind="rerank", metadata={"weight": 0.15}),
            VisualProcessStep(id="b", label="B", kind="review"),
        ],
        edges=[VisualProcessEdge(id="e", source="a", target="b")],
    )
    return graph.model_copy(update={"base_graph_hash": graph.definition_hash()})


def _patch(graph: VisualProcessGraph, operations: list[dict]) -> WorkflowPatch:
    return WorkflowPatch.model_validate(
        {
            "graph_id": graph.id,
            "definition_revision": graph.definition_revision,
            "base_graph_hash": graph.base_graph_hash,
            "operations": operations,
        }
    )


def test_patch_preview_applies_all_operations_to_clone_only() -> None:
    graph = _graph()
    patch = _patch(
        graph,
        [
            {
                "operation_id": "op-1",
                "op": "update_step_field",
                "step_id": "a",
                "path": "/metadata/weight",
                "expected_old_value": 0.15,
                "value": 0.25,
            },
            {
                "operation_id": "op-2",
                "op": "remove_edge",
                "edge_id": "e",
            },
            {
                "operation_id": "op-3",
                "op": "add_step",
                "temp_id": "new-step",
                "value": {"label": "New", "kind": "review"},
            },
            {
                "operation_id": "op-4",
                "op": "add_edge",
                "temp_id": "new-edge",
                "source": "b",
                "target": "new-step",
            },
        ],
    )
    preview = VisualProcessPatchService().preview(
        graph=graph,
        patch=patch,
        allowed_operations={"update_step_field", "remove_edge", "add_step", "add_edge"},
    )
    assert graph.step_by_id("a").metadata["weight"] == 0.15
    assert len(graph.steps) == 2
    assert preview.preview_graph["steps"][0]["metadata"]["weight"] == 0.25
    assert {item["id"] for item in preview.preview_graph["steps"]} == {"a", "b", "new-step"}
    assert {item["id"] for item in preview.preview_graph["edges"]} == {"new-edge"}
    assert preview.preview_graph_hash != preview.base_graph_hash
    assert preview.input_draft_hash == graph.definition_hash()
    assert preview.policy_reason_codes == (
        "patch_graph_clone_only",
        "patch_registry_fields_authorized",
        "patch_side_effects_absent",
    )
    assert preview.side_effects == ()


def test_patch_rejects_stale_revision_and_expected_value() -> None:
    graph = _graph()
    stale = _patch(
        graph,
        [
            {
                "operation_id": "op",
                "op": "update_step_field",
                "step_id": "a",
                "path": "/metadata/weight",
                "expected_old_value": 0.99,
                "value": 0.25,
            }
        ],
    )
    with pytest.raises(VisualProcessPatchRejected, match="patch_expected_old_value_conflict") as error:
        VisualProcessPatchService().preview(
            graph=graph,
            patch=stale,
            allowed_operations={"update_step_field"},
        )
    assert error.value.status_code == 409

    with pytest.raises(VisualProcessPatchRejected, match="patch_base_revision_conflict"):
        VisualProcessPatchService().preview(
            graph=graph,
            patch=stale.model_copy(update={"definition_revision": 3}),
            allowed_operations={"update_step_field"},
        )


def test_patch_rejects_unknown_field_secret_and_edge_breakage_atomically() -> None:
    graph = _graph()
    unknown = _patch(
        graph,
        [
            {
                "operation_id": "op",
                "op": "update_step_field",
                "step_id": "a",
                "path": "/metadata/unknown",
                "expected_old_value": None,
                "value": "x",
            }
        ],
    )
    with pytest.raises(VisualProcessPatchRejected, match="patch_field_not_allowed"):
        VisualProcessPatchService().preview(
            graph=graph,
            patch=unknown,
            allowed_operations={"update_step_field"},
        )

    remove_attached = _patch(
        graph,
        [{"operation_id": "op", "op": "remove_step", "step_id": "a"}],
    )
    with pytest.raises(VisualProcessPatchRejected, match="patch_step_has_attached_edges"):
        VisualProcessPatchService().preview(
            graph=graph,
            patch=remove_attached,
            allowed_operations={"remove_step"},
        )

    with pytest.raises(ValueError, match="inline_secret_forbidden"):
        _patch(
            graph,
            [
                {
                    "operation_id": "op",
                    "op": "add_step",
                    "temp_id": "secret-step",
                    "value": {"label": "Secret", "kind": "embed_api", "metadata": {"api_key": "secret"}},
                }
            ],
        )


def test_patch_side_effect_policy_records_capabilities_and_enforces_approval() -> None:
    graph = _graph()
    side_effect_patch = _patch(
        graph,
        [
            {
                "operation_id": "op-side-effect",
                "op": "add_step",
                "temp_id": "git-write",
                "value": {"label": "Git", "kind": "git_op"},
            }
        ],
    )
    preview = VisualProcessPatchService().preview(
        graph=graph,
        patch=side_effect_patch,
        allowed_operations={"add_step"},
    )
    assert preview.side_effects == ("write_files",)
    assert "patch_side_effect_configuration_reviewed" in preview.policy_reason_codes

    approval_patch = _patch(
        graph,
        [
            {
                "operation_id": "op-approval",
                "op": "add_step",
                "temp_id": "training",
                "value": {"label": "Train", "kind": "ml_intern_train_lora"},
            }
        ],
    )
    with pytest.raises(VisualProcessPatchRejected, match="patch_node_requires_gate"):
        VisualProcessPatchService().preview(
            graph=graph,
            patch=approval_patch,
            allowed_operations={"add_step"},
        )
