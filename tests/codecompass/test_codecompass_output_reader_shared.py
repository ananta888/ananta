from __future__ import annotations

import hashlib
import json

import pytest

from agent.services.codecompass_output_reader import get_codecompass_output_reader
from ananta_codecompass.output_reader import build_output_manifest
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)
from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader


def _canonical_jsonl_row(record):
    return (
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _write_single_domain_output_with_declaration(out, *, domain="alpha"):
    domain_key = hashlib.sha256(domain.encode("utf-8")).hexdigest()
    opaque_domain_key = f"sha256:{domain_key}"
    source_id = "source:file:alpha-a"
    semantic_node = {
        "id": "semantic:alpha:a",
        "kind": "semantic_node",
        CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: opaque_domain_key,
    }
    graph_node = {
        "id": source_id,
        "kind": "source_file",
        "file": f"{domain}/a.py",
        "path": f"{domain}/a.py",
    }
    declaration = {
        "source": source_id,
        "target": semantic_node["id"],
        "type": "declares",
        "directed": True,
    }
    semantic_node_row = _canonical_jsonl_row(semantic_node)
    declaration_row = _canonical_jsonl_row(declaration)
    (out / "semantic_nodes.jsonl").write_text(semantic_node_row, encoding="utf-8")
    (out / "semantic_edges.jsonl").write_text("", encoding="utf-8")
    (out / "graph_nodes.jsonl").write_text(_canonical_jsonl_row(graph_node), encoding="utf-8")
    (out / "graph_edges.jsonl").write_text(declaration_row, encoding="utf-8")
    domain_evidence = {
        "domain_key": opaque_domain_key,
        "status": "materialized",
        "source_file_count": 1,
        "semantic_file_count": 1,
        "semantic_node_count": 1,
        "semantic_edge_count": 0,
        "semantic_node_bytes": len(semantic_node_row.encode("utf-8")),
        "semantic_edge_bytes": 0,
        "graph_declaration_count": 1,
        "graph_declaration_bytes": len(declaration_row.encode("utf-8")),
        "truncated_graph_declaration_count": 0,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
    }
    semantic_budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": domain_evidence["semantic_node_bytes"],
        "semantic_edge_bytes": 0,
        "domain_admission": {
            "strategy": "top_level_domain_bounded_admission_v1",
            "top_level_domain_count": 1,
            "materialized_domain_count": 1,
            "omitted_domain_count": 0,
            "empty_domain_count": 0,
            "partition_count": 1,
            "evidence_count": 1,
            "evidence_truncated_count": 0,
            "max_partitions": 256,
            "max_total_bytes": 8 * 1024 * 1024,
            "aggregate_scope": "semantic_and_declaration_jsonl",
            "graph_declaration_bytes": domain_evidence["graph_declaration_bytes"],
            "final_graph_artifact_max_bytes": 32 * 1024 * 1024,
            "final_materializer_fail_closed": True,
            "domains": [domain_evidence],
        },
    }
    manifest = {
        "semantic_budget": semantic_budget,
        "partitioned_outputs": {
            "semantic_nodes": ["semantic_nodes.jsonl"],
            "semantic_edges": ["semantic_edges.jsonl"],
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, graph_node


def _write_partitioned_semantic_outputs(out, *, declared_partition_count=2):
    domain_keys = ("a" * 64, "b" * 64)
    node_names = [f"semantic_nodes.domain-{domain_key}.jsonl" for domain_key in domain_keys]
    edge_names = [f"semantic_edges.domain-{domain_key}.jsonl" for domain_key in domain_keys]
    node_rows = tuple(
        _canonical_jsonl_row(
            {
                "id": node_id,
                "kind": "semantic_node",
                CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: f"sha256:{domain_key}",
            }
        )
        for node_id, domain_key in zip(
            ("semantic:a", "semantic:longer-b"),
            domain_keys,
            strict=True,
        )
    )
    edge_rows = (
        _canonical_jsonl_row(
            {
                "source": "semantic:a",
                "target": "semantic:longer-b",
                "edge_type": "uses",
                CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: f"sha256:{domain_keys[0]}",
            }
        ),
        "",
    )
    for name, content in zip(node_names, node_rows, strict=True):
        (out / name).write_text(content, encoding="utf-8")
    for name, content in zip(edge_names, edge_rows, strict=True):
        (out / name).write_text(content, encoding="utf-8")
    node_bytes = [int((out / name).stat().st_size) for name in node_names]
    edge_bytes = [int((out / name).stat().st_size) for name in edge_names]
    materialized_count = declared_partition_count
    domains = [
        {
            "domain_key": f"sha256:{domain_keys[index]}",
            "status": "materialized",
            "source_file_count": 1,
            "semantic_file_count": 1,
            "semantic_node_count": (1 if declared_partition_count == 2 else 2),
            "semantic_edge_count": ((1 if index == 0 else 0) if declared_partition_count == 2 else 1),
            "semantic_node_bytes": (node_bytes[index] if declared_partition_count == 2 else sum(node_bytes)),
            "semantic_edge_bytes": (edge_bytes[index] if declared_partition_count == 2 else sum(edge_bytes)),
            "graph_declaration_count": 0,
            "graph_declaration_bytes": 0,
            "truncated_graph_declaration_count": 0,
            "truncated_node_count": 0,
            "truncated_edge_count": 0,
            "unresolved_edge_count": 0,
        }
        for index in range(materialized_count)
    ]
    semantic_budget = {
        "configured_max_records_per_partition": 5000,
        "max_records_per_partition": 5000,
        "max_bytes_per_partition": 4194304,
        "configuration_clamped": False,
        "truncated": False,
        "truncated_node_count": 0,
        "truncated_edge_count": 0,
        "unresolved_edge_count": 0,
        "semantic_node_bytes": sum(node_bytes),
        "semantic_edge_bytes": sum(edge_bytes),
        "domain_admission": {
            "strategy": "top_level_domain_bounded_admission_v1",
            "top_level_domain_count": materialized_count,
            "materialized_domain_count": materialized_count,
            "omitted_domain_count": 0,
            "empty_domain_count": 0,
            "partition_count": declared_partition_count,
            "evidence_count": materialized_count,
            "evidence_truncated_count": 0,
            "max_partitions": 256,
            "max_total_bytes": 8 * 1024 * 1024,
            "aggregate_scope": "semantic_and_declaration_jsonl",
            "graph_declaration_bytes": 0,
            "final_graph_artifact_max_bytes": 32 * 1024 * 1024,
            "final_materializer_fail_closed": True,
            "domains": domains,
        },
    }
    manifest = {
        "semantic_budget": semantic_budget,
        "partitioned_outputs": {
            "semantic_nodes": node_names,
            "semantic_edges": edge_names,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return manifest


def test_codecompass_output_reader_supports_hub_and_standalone_paths(tmp_path):
    out = tmp_path / "cc"
    out.mkdir()
    (out / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "idx-1", "kind": "java_type", "file": "src/A.java"}),
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
        json.dumps(
            {
                "source": "semantic:type:A",
                "target": "semantic:method:A.run",
                "edge_type": "declares",
            }
        )
        + "\n",
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
    loaded_output_kinds = {record["_provenance"]["output_kind"] for record in worker_payload["records"]}
    assert {"semantic_nodes", "semantic_edges"}.issubset(loaded_output_kinds)
    assert worker_payload["manifest"]["outputs"]["semantic_nodes"]["record_count"] == 1
    assert worker_payload["manifest"]["semantic_budget"] == semantic_budget
    assert "semantic_nodes" not in worker_payload["diagnostics"]["missing_outputs"]
    assert "semantic_edges" not in worker_payload["diagnostics"]["missing_outputs"]
    assert len(agent_payload["records"]) == len(worker_payload["records"])


def test_reader_loads_every_manifest_authoritative_semantic_shard(tmp_path):
    out = tmp_path / "partitioned"
    out.mkdir()
    raw_manifest = _write_partitioned_semantic_outputs(out)

    payload = CodeCompassOutputReader().load_from_output_dir(
        output_dir=out,
        record_output_kinds=("semantic_nodes", "semantic_edges"),
    )

    assert len(payload["records"]) == 3
    assert {
        record["id"] for record in payload["records"] if record["_provenance"]["output_kind"] == "semantic_nodes"
    } == {"semantic:a", "semantic:longer-b"}
    assert payload["manifest"]["outputs"]["semantic_nodes"] is None
    assert payload["manifest"]["outputs"]["semantic_edges"] is None
    assert len(payload["manifest"]["partitioned_outputs"]["semantic_nodes"]) == 2
    assert payload["manifest"]["semantic_budget"] == raw_manifest["semantic_budget"]


def test_partition_output_file_evidence_survives_manifest_roundtrip(tmp_path):
    out = tmp_path / "manifest-roundtrip"
    out.mkdir()
    raw_manifest = _write_partitioned_semantic_outputs(out)
    normalized_manifest = build_output_manifest(
        output_dir=out,
        semantic_budget=raw_manifest["semantic_budget"],
        partitioned_outputs=raw_manifest["partitioned_outputs"],
    )
    (out / "manifest.json").write_text(
        json.dumps(normalized_manifest),
        encoding="utf-8",
    )

    payload = CodeCompassOutputReader().load_from_output_dir(
        output_dir=out,
        record_output_kinds=("semantic_nodes", "semantic_edges"),
    )

    assert len(payload["records"]) == 3
    assert payload["manifest"]["partitioned_outputs"] == (normalized_manifest["partitioned_outputs"])


@pytest.mark.parametrize(
    ("field_name", "tampered_value", "reason"),
    (
        ("sha256", "0" * 64, "semantic_partition_hash_evidence_mismatch"),
        ("record_count", 99, "semantic_partition_count_evidence_mismatch"),
    ),
)
def test_reader_rejects_tampered_partition_output_file_evidence(
    tmp_path,
    field_name,
    tampered_value,
    reason,
):
    out = tmp_path / field_name
    out.mkdir()
    raw_manifest = _write_partitioned_semantic_outputs(out)
    normalized_manifest = build_output_manifest(
        output_dir=out,
        semantic_budget=raw_manifest["semantic_budget"],
        partitioned_outputs=raw_manifest["partitioned_outputs"],
    )
    normalized_manifest["partitioned_outputs"]["semantic_nodes"][0][field_name] = tampered_value
    (out / "manifest.json").write_text(
        json.dumps(normalized_manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=reason):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


@pytest.mark.parametrize("evidence_field", ("semantic_node_count", "semantic_node_bytes"))
def test_reader_binds_each_domain_evidence_to_its_exact_shard(
    tmp_path,
    evidence_field,
):
    out = tmp_path / evidence_field
    out.mkdir()
    manifest = _write_partitioned_semantic_outputs(out)
    domains = manifest["semantic_budget"]["domain_admission"]["domains"]
    if evidence_field.endswith("count"):
        domains[0][evidence_field] = 2
        domains[1][evidence_field] = 0
    else:
        domains[0][evidence_field], domains[1][evidence_field] = (
            domains[1][evidence_field],
            domains[0][evidence_field],
        )
    (out / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=(
            "semantic_domain_record_evidence_mismatch"
            if evidence_field.endswith("count")
            else "semantic_domain_byte_evidence_mismatch"
        ),
    ):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_rejects_domain_shards_without_partition_metadata(tmp_path):
    out = tmp_path / "unmanifested-shards"
    out.mkdir()
    shard = "a" * 64
    (out / f"semantic_nodes.domain-{shard}.jsonl").write_text(
        '{"id":"semantic:a"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="semantic_partition_manifest_missing"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes",),
        )


def test_reader_rejects_missing_manifest_authoritative_shard(tmp_path):
    out = tmp_path / "missing-shard"
    out.mkdir()
    manifest = _write_partitioned_semantic_outputs(out)
    missing_name = manifest["partitioned_outputs"]["semantic_nodes"][1]
    (out / missing_name).unlink()

    with pytest.raises(ValueError, match="semantic_partition_file_missing"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_rejects_symlinked_manifest_authoritative_shard(tmp_path):
    out = tmp_path / "symlinked-shard"
    out.mkdir()
    manifest = _write_partitioned_semantic_outputs(out)
    shard_name = manifest["partitioned_outputs"]["semantic_nodes"][0]
    shard_path = out / shard_name
    target_path = out / "semantic-node-target.jsonl"
    target_path.write_bytes(shard_path.read_bytes())
    shard_path.unlink()
    shard_path.symlink_to(target_path.name)

    with pytest.raises(ValueError, match="semantic_partition_file_missing"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_rejects_extra_canonical_domain_shard(tmp_path):
    out = tmp_path / "extra-shard"
    out.mkdir()
    _write_partitioned_semantic_outputs(out)
    (out / f"semantic_nodes.domain-{'c' * 64}.jsonl").write_text(
        '{"id":"semantic:extra"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="semantic_partition_manifest_mismatch"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


@pytest.mark.parametrize("output_kind", ("semantic_nodes", "semantic_edges"))
def test_reader_binds_every_semantic_record_to_its_domain_shard(
    tmp_path,
    output_kind,
):
    out = tmp_path / output_kind
    out.mkdir()
    _write_partitioned_semantic_outputs(out)
    filename = f"{output_kind}.domain-{'a' * 64}.jsonl"
    path = out / filename
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"sha256:{'a' * 64}",
            f"sha256:{'b' * 64}",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="semantic_partition_domain_marker_mismatch"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_binds_graph_declaration_evidence_to_source_domain(tmp_path):
    out = tmp_path / "declaration-domain"
    out.mkdir()
    _manifest, graph_node = _write_single_domain_output_with_declaration(out)

    payload = CodeCompassOutputReader().load_from_output_dir(
        output_dir=out,
        record_output_kinds=("semantic_nodes", "semantic_edges"),
    )

    assert payload["manifest"]["semantic_budget"]["domain_admission"]["graph_declaration_bytes"] > 0

    graph_node["file"] = "beta/a.py"
    graph_node["path"] = "beta/a.py"
    (out / "graph_nodes.jsonl").write_text(_canonical_jsonl_row(graph_node), encoding="utf-8")
    with pytest.raises(ValueError, match="semantic_graph_declaration_evidence_mismatch"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_preserves_whitespace_in_source_domain_identity(tmp_path):
    out = tmp_path / "whitespace-domain"
    out.mkdir()
    _write_single_domain_output_with_declaration(out, domain=" alpha ")

    payload = CodeCompassOutputReader().load_from_output_dir(
        output_dir=out,
        record_output_kinds=("semantic_nodes", "semantic_edges"),
    )

    expected_key = "sha256:" + hashlib.sha256(b" alpha ").hexdigest()
    assert payload["manifest"]["semantic_budget"]["domain_admission"]["domains"][0]["domain_key"] == expected_key


@pytest.mark.parametrize("evidence_field", ("graph_declaration_count", "graph_declaration_bytes"))
def test_reader_rejects_stale_graph_declaration_evidence(tmp_path, evidence_field):
    out = tmp_path / evidence_field
    out.mkdir()
    manifest, _graph_node = _write_single_domain_output_with_declaration(out)
    domain = manifest["semantic_budget"]["domain_admission"]["domains"][0]
    domain[evidence_field] += 1
    if evidence_field == "graph_declaration_bytes":
        manifest["semantic_budget"]["domain_admission"]["graph_declaration_bytes"] += 1
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="semantic_graph_declaration_evidence_mismatch"):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_reader_binds_declared_partition_count_to_both_shard_lists(tmp_path):
    out = tmp_path / "partition-count-mismatch"
    out.mkdir()
    _write_partitioned_semantic_outputs(out, declared_partition_count=1)

    with pytest.raises(
        ValueError,
        match="semantic_partition_count_evidence_mismatch",
    ):
        CodeCompassOutputReader().load_from_output_dir(
            output_dir=out,
            record_output_kinds=("semantic_nodes", "semantic_edges"),
        )


def test_default_reader_keeps_optional_semantic_partitions_out_of_candidate_records(
    tmp_path,
):
    out = tmp_path / "legacy"
    out.mkdir()
    (out / "index.jsonl").write_text('{"id":"idx-1","file":"src/A.java"}\n', encoding="utf-8")
    (out / "semantic_nodes.jsonl").write_text('{"id":"semantic:type:A","file":"src/A.java"}\n', encoding="utf-8")

    payload = CodeCompassOutputReader().load_from_output_dir(output_dir=out)

    assert {record["_provenance"]["output_kind"] for record in payload["records"]} == {"index"}
    assert "semantic_nodes" not in payload["manifest"]["outputs"]
    assert "semantic_nodes" not in payload["diagnostics"]["missing_outputs"]
    assert "semantic_edges" not in payload["diagnostics"]["missing_outputs"]


def test_semantic_budget_rejects_stale_partition_evidence(tmp_path):
    out = tmp_path / "stale-evidence"
    out.mkdir()
    (out / "semantic_nodes.jsonl").write_text('{"id":"semantic:type:A"}\n', encoding="utf-8")
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
    (out / "semantic_nodes.jsonl").write_text('{"id":"semantic:a"}\n{"id":"semantic:b"}\n', encoding="utf-8")
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
    (out / "semantic_nodes.jsonl").write_bytes(b"x" * (MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION + 1))

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
