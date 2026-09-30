"""Binding of declared semantic budgets to the partitions and graph rows on disk."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)


def _validate_semantic_partition_evidence(
    *,
    partitions: Mapping[str, Sequence[Mapping[str, Any]]],
    semantic_budget: Mapping[str, Any] | None,
    partition_metadata_present: bool,
) -> None:
    """Bind declared semantic budgets to the exact partitions on disk."""

    max_records = (
        int(semantic_budget["max_records_per_partition"])
        if semantic_budget is not None
        else MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION
    )
    max_bytes = (
        int(semantic_budget["max_bytes_per_partition"])
        if semantic_budget is not None
        else MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
    )
    for output_kind, byte_field in (
        ("semantic_nodes", "semantic_node_bytes"),
        ("semantic_edges", "semantic_edge_bytes"),
    ):
        entries = tuple(partitions.get(output_kind) or ())
        if not entries:
            if semantic_budget is not None:
                raise ValueError("semantic_partition_evidence_missing")
            continue
        actual_bytes = 0
        for entry in entries:
            path = Path(str(entry["path"]))
            try:
                partition_bytes = path.stat().st_size
            except OSError as exc:
                raise ValueError("semantic_partition_evidence_missing") from exc
            if int(entry["record_count"]) > max_records:
                raise ValueError("semantic_partition_record_budget_exceeded")
            if partition_bytes > max_bytes:
                raise ValueError("semantic_partition_byte_budget_exceeded")
            actual_bytes += partition_bytes
        if semantic_budget is not None and actual_bytes != int(semantic_budget[byte_field]):
            raise ValueError("semantic_partition_byte_evidence_mismatch")

    node_entries = tuple(partitions.get("semantic_nodes") or ())
    edge_entries = tuple(partitions.get("semantic_edges") or ())
    if partition_metadata_present:
        if not node_entries or len(node_entries) != len(edge_entries):
            raise ValueError("semantic_partition_pair_count_mismatch")
        node_names = {Path(str(entry["path"])).name for entry in node_entries}
        edge_names = {Path(str(entry["path"])).name for entry in edge_entries}
        expected_edge_names = {name.replace("semantic_nodes", "semantic_edges", 1) for name in node_names}
        if edge_names != expected_edge_names:
            raise ValueError("semantic_partition_pair_identity_mismatch")

    domain_admission = semantic_budget.get("domain_admission") if semantic_budget is not None else None
    if domain_admission is None:
        return
    if not partition_metadata_present:
        raise ValueError("semantic_partition_manifest_missing")
    expected_partition_count = int(domain_admission["partition_count"])
    if len(node_entries) != expected_partition_count or len(edge_entries) != expected_partition_count:
        raise ValueError("semantic_partition_count_evidence_mismatch")
    materialized_evidence = {
        str(entry["domain_key"]).removeprefix("sha256:"): entry
        for entry in domain_admission["domains"]
        if entry["status"] == "materialized"
    }
    node_by_name = {Path(str(entry["path"])).name: entry for entry in node_entries}
    edge_by_name = {Path(str(entry["path"])).name: entry for entry in edge_entries}
    if expected_partition_count == 1:
        if node_names != {"semantic_nodes.jsonl"} or edge_names != {"semantic_edges.jsonl"}:
            raise ValueError("semantic_partition_legacy_identity_mismatch")
        evidence = next(iter(materialized_evidence.values()), None)
        expected = (
            evidence
            if evidence is not None
            else {
                "semantic_node_count": 0,
                "semantic_edge_count": 0,
                "semantic_node_bytes": 0,
                "semantic_edge_bytes": 0,
            }
        )
        pairs = (
            (
                node_by_name["semantic_nodes.jsonl"],
                "semantic_node_count",
                "semantic_node_bytes",
            ),
            (
                edge_by_name["semantic_edges.jsonl"],
                "semantic_edge_count",
                "semantic_edge_bytes",
            ),
        )
        for entry, count_field, byte_field in pairs:
            if int(entry["record_count"]) != int(expected[count_field]):
                raise ValueError("semantic_domain_record_evidence_mismatch")
            if Path(str(entry["path"])).stat().st_size != int(expected[byte_field]):
                raise ValueError("semantic_domain_byte_evidence_mismatch")
        if evidence is not None:
            _validate_semantic_record_domain_keys(
                entries=(
                    node_by_name["semantic_nodes.jsonl"],
                    edge_by_name["semantic_edges.jsonl"],
                ),
                expected_domain_key=str(evidence["domain_key"]),
            )
        return

    shard_domain_keys = {name.removeprefix("semantic_nodes.domain-").removesuffix(".jsonl") for name in node_names}
    if shard_domain_keys != set(materialized_evidence):
        raise ValueError("semantic_partition_domain_evidence_mismatch")
    for domain_key, evidence in materialized_evidence.items():
        node_name = f"semantic_nodes.domain-{domain_key}.jsonl"
        edge_name = f"semantic_edges.domain-{domain_key}.jsonl"
        for entry, count_field, byte_field in (
            (
                node_by_name[node_name],
                "semantic_node_count",
                "semantic_node_bytes",
            ),
            (
                edge_by_name[edge_name],
                "semantic_edge_count",
                "semantic_edge_bytes",
            ),
        ):
            if int(entry["record_count"]) != int(evidence[count_field]):
                raise ValueError("semantic_domain_record_evidence_mismatch")
            if Path(str(entry["path"])).stat().st_size != int(evidence[byte_field]):
                raise ValueError("semantic_domain_byte_evidence_mismatch")
        _validate_semantic_record_domain_keys(
            entries=(node_by_name[node_name], edge_by_name[edge_name]),
            expected_domain_key=str(evidence["domain_key"]),
        )


def _validate_semantic_record_domain_keys(
    *,
    entries: Sequence[Mapping[str, Any]],
    expected_domain_key: str,
) -> None:
    for entry in entries:
        for record in entry.get("_records", ()):
            if not isinstance(record, Mapping):
                raise ValueError("semantic_partition_domain_marker_invalid")
            if record.get(CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD) != expected_domain_key:
                raise ValueError("semantic_partition_domain_marker_mismatch")


def _canonical_jsonl_record_bytes(record: Mapping[str, Any]) -> int:
    try:
        payload = json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic_graph_declaration_record_invalid") from exc
    return len((payload + "\n").encode("utf-8"))


def _source_file_domain_key(record: Mapping[str, Any]) -> str | None:
    if record.get("kind") != "source_file":
        return None
    raw_path: object = None
    for field in ("file", "path", "relative_path", "source_path"):
        if record.get(field):
            raw_path = record[field]
            break
    if raw_path is None:
        for provenance_field in ("provenance", "_provenance"):
            provenance = record.get(provenance_field)
            if not isinstance(provenance, Mapping):
                continue
            raw_path = provenance.get("file") or provenance.get("path")
            if raw_path:
                break
    path = str(raw_path or "")
    if not path:
        return None
    head, separator, _tail = path.partition("/")
    if separator and head:
        return codecompass_semantic_domain_key(head)
    return codecompass_semantic_repository_root_domain_key()


def _validate_graph_declaration_evidence(
    *,
    graph_nodes: Mapping[str, Any] | None,
    graph_edges: Mapping[str, Any] | None,
    semantic_budget: Mapping[str, Any] | None,
) -> None:
    """Bind per-domain declaration evidence to canonical graph JSONL rows."""

    domain_admission = semantic_budget.get("domain_admission") if semantic_budget is not None else None
    if domain_admission is None:
        return
    expected = {
        str(domain["domain_key"]): (
            int(domain["graph_declaration_count"]),
            int(domain["graph_declaration_bytes"]),
        )
        for domain in domain_admission["domains"]
        if domain["status"] == "materialized"
    }
    expected_total_count = sum(count for count, _bytes in expected.values())
    if expected_total_count and (graph_nodes is None or graph_edges is None):
        raise ValueError("semantic_graph_declaration_evidence_missing")

    source_domains: dict[str, str] = {}
    for node in (graph_nodes or {}).get("_records", ()):
        if not isinstance(node, Mapping):
            continue
        domain_key = _source_file_domain_key(node)
        if domain_key is None:
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        existing_domain = source_domains.setdefault(node_id, domain_key)
        if existing_domain != domain_key:
            raise ValueError("semantic_graph_source_domain_conflict")

    actual: dict[str, tuple[int, int]] = {}
    for edge in (graph_edges or {}).get("_records", ()):
        if not isinstance(edge, Mapping):
            continue
        if str(edge.get("type") or edge.get("edge_type") or "").strip() != "declares":
            continue
        source_id = str(edge.get("source") or edge.get("source_id") or "").strip()
        domain_key = source_domains.get(source_id)
        if domain_key is None:
            raise ValueError("semantic_graph_declaration_source_evidence_missing")
        count, byte_count = actual.get(domain_key, (0, 0))
        actual[domain_key] = (
            count + 1,
            byte_count + _canonical_jsonl_record_bytes(edge),
        )

    if actual != {domain_key: evidence for domain_key, evidence in expected.items() if evidence != (0, 0)}:
        raise ValueError("semantic_graph_declaration_evidence_mismatch")
    if sum(byte_count for _count, byte_count in actual.values()) != int(domain_admission["graph_declaration_bytes"]):
        raise ValueError("semantic_graph_declaration_byte_evidence_mismatch")

