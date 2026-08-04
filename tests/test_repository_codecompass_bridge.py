from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.retrieval.repository_codecompass_bridge import (
    RepositoryCodeCompassBridge,
    _BoundedSemanticEdgeSpool,
)


class _SemanticGraphStub:
    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del path, content
        return {
            "nodes": [
                {
                    "id": "type:Example",
                    "kind": "data_record",
                    "attributes": {
                        "fields": [{"name": "value", "type": "str"}],
                        "methods": [
                            {
                                "name": "render",
                                "parameters": [{"name": "prefix", "type": "str"}],
                            }
                        ],
                    },
                },
                {
                    "id": "method:Example.render",
                    "kind": "function_signature",
                    "attributes": {
                        "name": "render",
                        "parameters": [{"name": "prefix", "type": "str"}],
                    },
                },
            ],
            "edges": [
                {
                    "source": "type:Example",
                    "target": "method:Example.render",
                    "edge_type": "declares",
                }
            ],
            "diagnostics": [],
        }


class _OversizedSemanticGraphStub:
    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del path, content
        return {
            "nodes": [
                {"id": "semantic:a", "kind": "semantic_node"},
                {"id": "semantic:b", "kind": "semantic_node"},
                {"id": "semantic:c", "kind": "semantic_node"},
            ],
            "edges": [
                {"source": "semantic:a", "target": "semantic:b", "edge_type": "declares"},
                {"source": "semantic:a", "target": "semantic:c", "edge_type": "uses_type"},
                {"source": "semantic:c", "target": "semantic:external", "edge_type": "uses_type"},
            ],
            "diagnostics": [],
        }


class _UnresolvedBeforeValidSemanticGraphStub:
    def __init__(self) -> None:
        self.call_count = 0

    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del path, content
        self.call_count += 1
        unresolved = [
            {
                "source": "semantic:a",
                "target": f"semantic:missing:{index}",
                "edge_type": "uses_type",
            }
            for index in range(8)
        ]
        return {
            "nodes": [
                {"id": "semantic:a", "kind": "semantic_node"},
                {"id": "semantic:b", "kind": "semantic_node"},
            ],
            "edges": [
                *unresolved,
                {
                    "source": "semantic:a",
                    "target": "semantic:b",
                    "edge_type": "declares",
                },
            ],
            "diagnostics": [],
        }


class _SemanticFileEndpointGraphStub:
    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del content
        module_id = "semantic:typescript:module:@angular/core"
        return {
            "nodes": [
                {
                    "id": module_id,
                    "kind": "semantic_node",
                    "file": path,
                }
            ],
            "edges": [
                {
                    "source": f"semantic:typescript:file:{path}",
                    "target": module_id,
                    "edge_type": "imports",
                }
            ],
            "diagnostics": [],
        }


class _DeferredCrossFileSemanticGraphStub:
    def __init__(self, *, reverse_candidates: bool) -> None:
        self.reverse_candidates = reverse_candidates
        self.call_count = 0

    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del content
        self.call_count += 1
        if path == "src/b.ts":
            return {
                "nodes": [{"id": "semantic:b", "kind": "semantic_node"}],
                "edges": [],
                "diagnostics": [],
            }
        candidates = [
            {
                "source": "semantic:a",
                "target": f"semantic:missing:{index}",
                "edge_type": "uses_type",
            }
            for index in range(6)
        ]
        candidates.append(
            {
                "source": "semantic:a",
                "target": "semantic:b",
                "edge_type": "uses_type",
            }
        )
        if self.reverse_candidates:
            candidates.reverse()
        return {
            "nodes": [{"id": "semantic:a", "kind": "semantic_node"}],
            "edges": candidates,
            "diagnostics": [],
        }


def test_bridge_omits_redundant_nested_method_snapshots(tmp_path: Path) -> None:
    RepositoryCodeCompassBridge(_SemanticGraphStub()).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "class Example: pass",
                "metadata": {"relative_path": "src/example.py"},
            }
        ],
        output_dir=tmp_path,
    )

    nodes = [
        json.loads(line)
        for line in (tmp_path / "semantic_nodes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    type_node = next(node for node in nodes if node["id"] == "type:Example")
    method_node = next(node for node in nodes if node["id"] == "method:Example.render")

    assert "methods" not in type_node["attributes"]
    assert type_node["attributes"]["fields"] == [{"name": "value", "type": "str"}]
    assert method_node["attributes"]["parameters"] == [
        {"name": "prefix", "type": "str"}
    ]


def test_bridge_bounds_semantic_partitions_and_keeps_every_edge_endpoint_closed(
    tmp_path: Path,
) -> None:
    result = RepositoryCodeCompassBridge(
        _OversizedSemanticGraphStub(),
        max_semantic_records_per_partition=2,
    ).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "class Example: pass",
                "metadata": {"relative_path": "src/example.py"},
            }
        ],
        output_dir=tmp_path,
    )

    def rows(name: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in (tmp_path / name).read_text(encoding="utf-8").splitlines()
        ]

    nodes = [*rows("graph_nodes.jsonl"), *rows("semantic_nodes.jsonl")]
    edges = [*rows("graph_edges.jsonl"), *rows("semantic_edges.jsonl")]
    node_ids = {str(node["id"]) for node in nodes}

    assert {node["id"] for node in rows("semantic_nodes.jsonl")} == {
        "semantic:a",
        "semantic:b",
    }
    assert len(rows("semantic_edges.jsonl")) == 1
    assert all(
        str(edge.get("source") or edge.get("source_id")) in node_ids
        and str(edge.get("target") or edge.get("target_id")) in node_ids
        for edge in edges
    )
    budget = result["semantic_budget"]
    expected_partition_budget = {
        "configured_max_records_per_partition": 2,
        "max_records_per_partition": 2,
        "max_bytes_per_partition": 4 * 1024 * 1024,
        "configuration_clamped": False,
        "truncated": True,
        "truncated_node_count": 1,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 2,
        "semantic_node_bytes": sum(
            len(line.encode("utf-8")) + 1
            for line in (tmp_path / "semantic_nodes.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ),
        "semantic_edge_bytes": sum(
            len(line.encode("utf-8")) + 1
            for line in (tmp_path / "semantic_edges.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ),
    }
    assert {
        key: budget[key] for key in expected_partition_budget
    } == expected_partition_budget
    assert budget["candidate_edge_record_limit"] == 20_000
    assert budget["candidate_edge_byte_limit"] == 16 * 1024 * 1024
    assert budget["candidate_edge_count"] == 2
    assert 0 < budget["candidate_edge_bytes"] <= 16 * 1024 * 1024
    assert budget["truncated_candidate_edge_count"] == 0


def test_bridge_does_not_let_unresolved_candidates_crowd_out_bound_edges(
    tmp_path: Path,
) -> None:
    semantic_graph = _UnresolvedBeforeValidSemanticGraphStub()
    result = RepositoryCodeCompassBridge(
        semantic_graph,
        max_semantic_records_per_partition=2,
    ).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "export class Example {}",
                "metadata": {"relative_path": "src/example.ts"},
            }
        ],
        output_dir=tmp_path,
    )

    edges = [
        json.loads(line)
        for line in (tmp_path / "semantic_edges.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert [(edge["source"], edge["target"]) for edge in edges] == [
        ("semantic:a", "semantic:b")
    ]
    assert result["semantic_budget"]["unresolved_edge_count"] == 8
    assert result["semantic_budget"]["truncated_edge_count"] == 0
    assert semantic_graph.call_count == 1


def test_bridge_spool_overflow_is_bounded_and_keeps_direct_valid_edges(
    tmp_path: Path,
) -> None:
    semantic_graph = _UnresolvedBeforeValidSemanticGraphStub()
    result = RepositoryCodeCompassBridge(
        semantic_graph,
        max_semantic_records_per_partition=2,
        max_semantic_edge_candidates=2,
        max_semantic_edge_candidate_bytes=1024 * 1024,
    ).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "export class Example {}",
                "metadata": {"relative_path": "src/example.ts"},
            }
        ],
        output_dir=tmp_path,
    )

    edges = [
        json.loads(line)
        for line in (tmp_path / "semantic_edges.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    budget = result["semantic_budget"]

    assert [(edge["source"], edge["target"]) for edge in edges] == [
        ("semantic:a", "semantic:b")
    ]
    assert semantic_graph.call_count == 1
    assert budget["candidate_edge_count"] == 2
    assert budget["truncated_candidate_edge_count"] == 6
    assert budget["truncated_edge_count"] == 6
    assert budget["unresolved_edge_count"] == 2
    assert budget["truncated"] is True


def test_bridge_deferred_reservoir_is_order_independent_and_keeps_cross_file_edge(
    tmp_path: Path,
) -> None:
    outputs: list[list[dict[str, Any]]] = []
    for reverse_candidates in (False, True):
        output_dir = tmp_path / str(reverse_candidates)
        semantic_graph = _DeferredCrossFileSemanticGraphStub(
            reverse_candidates=reverse_candidates
        )
        result = RepositoryCodeCompassBridge(
            semantic_graph,
            max_semantic_records_per_partition=2,
            max_semantic_edge_candidates=2,
            max_semantic_edge_candidate_bytes=1024,
        ).build_outputs(
            source_id="repo",
            records=[
                {
                    "content": "export class A {}",
                    "metadata": {"relative_path": "src/a.ts"},
                },
                {
                    "content": "export class B {}",
                    "metadata": {"relative_path": "src/b.ts"},
                },
            ],
            output_dir=output_dir,
        )
        outputs.append(
            [
                json.loads(line)
                for line in (output_dir / "semantic_edges.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        )
        budget = result["semantic_budget"]
        assert semantic_graph.call_count == 2
        assert budget["candidate_edge_count"] <= 2
        assert budget["candidate_edge_bytes"] <= 1024
        assert budget["truncated_candidate_edge_count"] == 5

    assert outputs[0] == outputs[1]
    assert [(edge["source"], edge["target"]) for edge in outputs[0]] == [
        ("semantic:a", "semantic:b")
    ]


def test_deferred_reservoir_is_order_independent_for_variable_record_sizes() -> None:
    candidates = [
        {
            "source": "semantic:a",
            "target": f"semantic:target:{index}",
            "edge_type": "uses_type",
            "attributes": {"evidence": "x" * evidence_size},
        }
        for index, evidence_size in enumerate((0, 8, 32, 160, 320))
    ]
    emitted = [*candidates, candidates[0], candidates[-1]]
    snapshots: list[tuple[list[dict[str, Any]], int, int]] = []
    for ordered in (emitted, list(reversed(emitted))):
        spool = _BoundedSemanticEdgeSpool(max_records=3, max_bytes=600)
        for edge in ordered:
            spool.append(edge)
        snapshots.append(
            (
                list(spool.records()),
                spool.truncated_edge_count,
                spool.byte_count,
            )
        )

    assert snapshots[0] == snapshots[1]
    assert snapshots[0][1] == len(candidates) - len(snapshots[0][0])
    assert snapshots[0][2] <= 600


def test_deferred_reservoir_does_not_report_duplicates_as_truncated() -> None:
    edge = {
        "source": "semantic:a",
        "target": "semantic:b",
        "edge_type": "uses_type",
    }
    spool = _BoundedSemanticEdgeSpool(max_records=2, max_bytes=1024)

    spool.append(edge)
    spool.append(edge)

    assert spool.record_count == 1
    assert spool.truncated_edge_count == 0


def test_bridge_binds_adapter_file_endpoint_to_grounded_repository_file_node(
    tmp_path: Path,
) -> None:
    RepositoryCodeCompassBridge(_SemanticFileEndpointGraphStub()).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "import {Component} from '@angular/core';",
                "metadata": {"relative_path": "src/example.ts"},
            }
        ],
        output_dir=tmp_path,
    )

    graph_nodes = [
        json.loads(line)
        for line in (tmp_path / "graph_nodes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    source_file = next(node for node in graph_nodes if node["kind"] == "source_file")
    semantic_edge = json.loads(
        (tmp_path / "semantic_edges.jsonl").read_text(encoding="utf-8")
    )

    assert semantic_edge["source"] == source_file["id"]
    assert semantic_edge["target"] == "semantic:typescript:module:@angular/core"
    assert semantic_edge["attributes"]["source_endpoint_original"] == (
        "semantic:typescript:file:src/example.ts"
    )
    assert semantic_edge["attributes"]["source_endpoint_binding"] == (
        "repository_source_file"
    )


def test_bridge_clamps_configured_record_budget_to_graph_artifact_envelope(
    tmp_path: Path,
) -> None:
    result = RepositoryCodeCompassBridge(
        _SemanticGraphStub(),
        max_semantic_records_per_partition=100_000,
    ).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "class Example: pass",
                "metadata": {"relative_path": "src/example.py"},
            }
        ],
        output_dir=tmp_path,
    )

    assert result["semantic_budget"]["configured_max_records_per_partition"] == (
        100_000
    )
    assert result["semantic_budget"]["max_records_per_partition"] == 5_000
    assert result["semantic_budget"]["configuration_clamped"] is True
    assert (tmp_path / "semantic_nodes.jsonl").stat().st_size <= 4 * 1024 * 1024
    assert (tmp_path / "semantic_edges.jsonl").stat().st_size <= 4 * 1024 * 1024
