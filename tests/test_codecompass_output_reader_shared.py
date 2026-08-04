from __future__ import annotations

import json

import pytest

from agent.services.codecompass_output_reader import get_codecompass_output_reader
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
)
from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader


def test_codecompass_output_reader_supports_hub_and_standalone_paths(tmp_path):
    out = tmp_path / "cc"
    out.mkdir()
    (out / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {"id": "idx-1", "kind": "java_type", "file": "src/A.java"}
                ),
                "{bad-json}",
            ]
        ),
        encoding="utf-8",
    )
    (out / "details.jsonl").write_text(
        json.dumps({"id": "det-1", "kind": "method", "file": "src/A.java"}),
        encoding="utf-8",
    )
    (out / "semantic_nodes.jsonl").write_text(
        json.dumps({"id": "semantic:type:A", "kind": "data_record", "file": "src/A.java"}) + "\n",
        encoding="utf-8",
    )
    (out / "semantic_edges.jsonl").write_text(
        json.dumps({
            "source": "semantic:type:A",
            "target": "semantic:method:A.run",
            "edge_type": "declares",
        }) + "\n",
        encoding="utf-8",
    )
    semantic_budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": (out / "semantic_nodes.jsonl").stat().st_size,
        "semantic_edge_bytes": (out / "semantic_edges.jsonl").stat().st_size,
    }
    (out / "manifest.json").write_text(
        json.dumps({"semantic_budget": semantic_budget}),
        encoding="utf-8",
    )

    worker_reader = CodeCompassOutputReader()
    agent_reader = get_codecompass_output_reader()
    output_kinds = (
        "index",
        "details",
        "context",
        "embedding",
        "relations",
        "graph_nodes",
        "graph_edges",
        "semantic_nodes",
        "semantic_edges",
    )
    worker_payload = worker_reader.load_from_output_dir(
        output_dir=out,
        codecompass_version="1.0.0",
        profile_name="java",
        source_scope="repo",
        generated_at="now",
        record_output_kinds=output_kinds,
    )
    agent_payload = agent_reader.load_from_output_dir(
        output_dir=out,
        codecompass_version="1.0.0",
        profile_name="java",
        source_scope="repo",
        generated_at="now",
        record_output_kinds=output_kinds,
    )

    assert worker_payload["manifest"]["schema"] == "codecompass_output_manifest.v1"
    assert agent_payload["manifest"]["schema"] == "codecompass_output_manifest.v1"
    assert worker_payload["standalone_compatible"] is True
    assert worker_payload["diagnostics"]["malformed_line_count"] == 1
    assert "embedding" in worker_payload["diagnostics"]["missing_outputs"]
    assert worker_payload["records"]
    assert worker_payload["records"][0]["_provenance"]["manifest_hash"] == worker_payload["manifest"]["manifest_hash"]
    loaded_output_kinds = {
        record["_provenance"]["output_kind"]
        for record in worker_payload["records"]
    }
    assert {"semantic_nodes", "semantic_edges"}.issubset(loaded_output_kinds)
    assert worker_payload["manifest"]["outputs"]["semantic_nodes"]["record_count"] == 1
    assert worker_payload["manifest"]["semantic_budget"] == semantic_budget
    assert "semantic_nodes" not in worker_payload["diagnostics"]["missing_outputs"]
    assert "semantic_edges" not in worker_payload["diagnostics"]["missing_outputs"]
    assert len(agent_payload["records"]) == len(worker_payload["records"])


def test_default_reader_keeps_optional_semantic_partitions_out_of_candidate_records(
    tmp_path,
):
    out = tmp_path / "legacy"
    out.mkdir()
    (out / "index.jsonl").write_text(
        '{"id":"idx-1","file":"src/A.java"}\n', encoding="utf-8"
    )
    (out / "semantic_nodes.jsonl").write_text(
        '{"id":"semantic:type:A","file":"src/A.java"}\n', encoding="utf-8"
    )

    payload = CodeCompassOutputReader().load_from_output_dir(output_dir=out)

    assert {
        record["_provenance"]["output_kind"] for record in payload["records"]
    } == {"index"}
    assert "semantic_nodes" not in payload["manifest"]["outputs"]
    assert "semantic_nodes" not in payload["diagnostics"]["missing_outputs"]
    assert "semantic_edges" not in payload["diagnostics"]["missing_outputs"]


def test_semantic_budget_rejects_stale_partition_evidence(tmp_path):
    out = tmp_path / "stale-evidence"
    out.mkdir()
    (out / "semantic_nodes.jsonl").write_text(
        '{"id":"semantic:type:A"}\n', encoding="utf-8"
    )
    (out / "semantic_edges.jsonl").write_text("", encoding="utf-8")
    budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": 1,
        "semantic_edge_bytes": 0,
    }

    with pytest.raises(ValueError, match="semantic_partition_byte_evidence_mismatch"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            semantic_budget=budget,
        )


def test_semantic_budget_checks_actual_partition_record_count(tmp_path):
    out = tmp_path / "record-overflow"
    out.mkdir()
    (out / "semantic_nodes.jsonl").write_text(
        '{"id":"semantic:a"}\n{"id":"semantic:b"}\n', encoding="utf-8"
    )
    (out / "semantic_edges.jsonl").write_text("", encoding="utf-8")
    budget = {
        "configured_max_records_per_partition": 1,
        "max_records_per_partition": 1,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": (out / "semantic_nodes.jsonl").stat().st_size,
        "semantic_edge_bytes": 0,
    }

    with pytest.raises(ValueError, match="semantic_partition_record_budget_exceeded"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            semantic_budget=budget,
        )


def test_semantic_partition_is_size_preflighted_without_budget(tmp_path):
    out = tmp_path / "unbudgeted-overflow"
    out.mkdir()
    (out / "semantic_nodes.jsonl").write_bytes(
        b"x" * (MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION + 1)
    )

    with pytest.raises(ValueError, match="semantic_partition_byte_budget_exceeded"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes",),
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("max_records_per_partition", True),
        ("semantic_node_bytes", 1.5),
        ("truncated", "false"),
    ),
)
def test_semantic_budget_rejects_schema_invalid_runtime_types(
    tmp_path,
    field_name,
    invalid_value,
):
    out = tmp_path / field_name
    out.mkdir()
    (out / "semantic_nodes.jsonl").write_text("", encoding="utf-8")
    (out / "semantic_edges.jsonl").write_text("", encoding="utf-8")
    budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": 0,
        "semantic_edge_bytes": 0,
    }
    budget[field_name] = invalid_value

    with pytest.raises(ValueError, match=f"invalid_{field_name}"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            semantic_budget=budget,
        )
