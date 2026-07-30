from __future__ import annotations

import pytest

from agent.services.model_intelligence_graph_service import (
    ModelGraphBuilder,
    ModelGraphError,
    ModelGraphQueryPolicy,
    ModelGraphQueryService,
)


def _analysis(tensor_count: int = 3) -> dict[str, object]:
    tensors = []
    for index in range(tensor_count):
        tensors.append(
            {
                "name": f"model.layers.{index}.weight",
                "module": f"model.layers.{index}",
                "layer_index": index,
                "dtype": "F32",
                "shape": [2],
                "parameter_count": 2,
                "size_bytes": 8,
                "relative_path": "model.safetensors",
            }
        )
    return {
        "schema_version": "static_analysis.v1",
        "status": "available",
        "tensor_count": tensor_count,
        "parameter_count": tensor_count * 2,
        "total_tensor_bytes": tensor_count * 8,
        "dtypes": {"F32": tensor_count},
        "modules": {},
        "tensors": tensors,
        "content_digest": "0" * 64,
    }


def test_model_graph_is_stable_and_separate_from_source_graph() -> None:
    builder = ModelGraphBuilder()

    first = builder.from_static_analysis(
        model_id="model:test:v1",
        analysis=_analysis(),
    ).to_dict()
    repeated = builder.from_static_analysis(
        model_id="model:test:v1",
        analysis=_analysis(),
    ).to_dict()

    assert first == repeated
    assert first["schema_version"] == "model_graph.v1"
    assert {node["kind"] for node in first["nodes"]} == {
        "model",
        "module",
        "layer",
        "tensor",
    }
    assert "domain_graph_artifact.v1" not in str(first)


def test_graph_query_enforces_server_limits() -> None:
    artifact = ModelGraphBuilder().from_static_analysis(
        model_id="model:test:v1",
        analysis=_analysis(20),
    )
    service = ModelGraphQueryService(
        artifact,
        ModelGraphQueryPolicy(max_nodes=5, max_page_size=2),
    )
    root = next(node for node in artifact.nodes if node.kind == "model")

    result = service.traverse(
        start_node_id=root.node_id,
        max_depth=2,
        max_nodes=5,
        page_size=2,
    )

    assert len(result.nodes) <= 2
    assert result.truncated is True
    with pytest.raises(ModelGraphError) as captured:
        service.traverse(
            start_node_id=root.node_id,
            max_nodes=6,
        )
    assert captured.value.code == "model_graph_node_limit_invalid"


def test_graph_query_rejects_invalid_cursor() -> None:
    artifact = ModelGraphBuilder().from_static_analysis(
        model_id="model:test:v1",
        analysis=_analysis(),
    )
    root = next(node for node in artifact.nodes if node.kind == "model")

    with pytest.raises(ModelGraphError) as captured:
        ModelGraphQueryService(artifact).traverse(
            start_node_id=root.node_id,
            cursor="../unsafe",
        )

    assert captured.value.code == "model_graph_cursor_invalid"
