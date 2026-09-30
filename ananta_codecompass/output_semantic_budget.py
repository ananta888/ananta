"""Validation of declared semantic-translation budgets and domain admission evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_codecompass.output_capability_normalization import _non_negative_int, _strict_bool
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
)

_SEMANTIC_DOMAIN_ADMISSION_STRATEGY = "top_level_domain_bounded_admission_v1"
_SEMANTIC_DOMAIN_STATUSES = frozenset(
    {
        "materialized",
        "aggregate_byte_limit",
        "partition_limit",
        "per_partition_limit",
        "no_semantic_records",
    }
)


def _normalize_semantic_domain_admission(
    value: object,
    *,
    semantic_node_bytes: int,
    semantic_edge_bytes: int,
    truncated_node_count: int,
    truncated_edge_count: int,
    unresolved_edge_count: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_semantic_domain_admission")
    raw = dict(value)
    expected_fields = {
        "strategy",
        "top_level_domain_count",
        "materialized_domain_count",
        "omitted_domain_count",
        "empty_domain_count",
        "partition_count",
        "evidence_count",
        "evidence_truncated_count",
        "max_partitions",
        "max_total_bytes",
        "aggregate_scope",
        "graph_declaration_bytes",
        "final_graph_artifact_max_bytes",
        "final_materializer_fail_closed",
        "domains",
    }
    if set(raw) != expected_fields:
        raise ValueError("invalid_semantic_domain_admission_fields")
    if raw["strategy"] != _SEMANTIC_DOMAIN_ADMISSION_STRATEGY:
        raise ValueError("invalid_semantic_domain_admission_strategy")
    if raw["aggregate_scope"] != "semantic_and_declaration_jsonl":
        raise ValueError("invalid_semantic_domain_admission_scope")
    domain_count = _non_negative_int(
        raw["top_level_domain_count"],
        field_name="semantic_top_level_domain_count",
    )
    materialized_count = _non_negative_int(
        raw["materialized_domain_count"],
        field_name="semantic_materialized_domain_count",
    )
    omitted_count = _non_negative_int(
        raw["omitted_domain_count"],
        field_name="semantic_omitted_domain_count",
    )
    empty_count = _non_negative_int(
        raw["empty_domain_count"],
        field_name="semantic_empty_domain_count",
    )
    partition_count = _non_negative_int(
        raw["partition_count"],
        field_name="semantic_partition_count",
    )
    evidence_count = _non_negative_int(
        raw["evidence_count"],
        field_name="semantic_domain_evidence_count",
    )
    evidence_truncated_count = _non_negative_int(
        raw["evidence_truncated_count"],
        field_name="semantic_domain_evidence_truncated_count",
    )
    max_partitions = _non_negative_int(
        raw["max_partitions"],
        field_name="semantic_max_partitions",
    )
    max_total_bytes = _non_negative_int(
        raw["max_total_bytes"],
        field_name="semantic_max_total_bytes",
    )
    graph_declaration_bytes = _non_negative_int(
        raw["graph_declaration_bytes"],
        field_name="semantic_graph_declaration_bytes",
    )
    final_graph_max_bytes = _non_negative_int(
        raw["final_graph_artifact_max_bytes"],
        field_name="semantic_final_graph_artifact_max_bytes",
    )
    if not 1 <= domain_count <= 20_000:
        raise ValueError("invalid_semantic_top_level_domain_count")
    if materialized_count + omitted_count + empty_count != domain_count:
        raise ValueError("invalid_semantic_domain_admission_counts")
    if (
        max_partitions != MAX_CODECOMPASS_SEMANTIC_PARTITIONS
        or not 1 <= partition_count <= max_partitions
        or partition_count != max(1, materialized_count)
    ):
        raise ValueError("invalid_semantic_partition_count")
    if (
        max_total_bytes != MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES
        or semantic_node_bytes + semantic_edge_bytes + graph_declaration_bytes > max_total_bytes
    ):
        raise ValueError("semantic_total_byte_budget_exceeded")
    if final_graph_max_bytes != MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES:
        raise ValueError("invalid_semantic_final_graph_artifact_limit")
    if not _strict_bool(
        raw["final_materializer_fail_closed"],
        field_name="semantic_final_materializer_fail_closed",
    ):
        raise ValueError("semantic_final_materializer_must_fail_closed")
    raw_domains = raw["domains"]
    if (
        not isinstance(raw_domains, Sequence)
        or isinstance(raw_domains, (str, bytes))
        or len(raw_domains) > MAX_CODECOMPASS_SEMANTIC_PARTITIONS
        or len(raw_domains) != evidence_count
        or evidence_count + evidence_truncated_count != domain_count
    ):
        raise ValueError("invalid_semantic_domain_evidence_count")

    normalized_domains: list[dict[str, Any]] = []
    seen_domain_keys: set[str] = set()
    materialized_evidence_count = 0
    materialized_node_bytes = 0
    materialized_edge_bytes = 0
    materialized_declaration_bytes = 0
    evidenced_truncated_nodes = 0
    evidenced_truncated_edges = 0
    evidenced_unresolved_edges = 0
    domain_fields = {
        "domain_key",
        "status",
        "source_file_count",
        "semantic_file_count",
        "semantic_node_count",
        "semantic_edge_count",
        "semantic_node_bytes",
        "semantic_edge_bytes",
        "graph_declaration_count",
        "graph_declaration_bytes",
        "truncated_graph_declaration_count",
        "truncated_node_count",
        "truncated_edge_count",
        "unresolved_edge_count",
    }
    for raw_domain in raw_domains:
        if not isinstance(raw_domain, Mapping):
            raise ValueError("invalid_semantic_domain_evidence")
        domain = dict(raw_domain)
        if set(domain) != domain_fields:
            raise ValueError("invalid_semantic_domain_evidence_fields")
        domain_key = domain["domain_key"]
        status = domain["status"]
        if (
            not isinstance(domain_key, str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", domain_key) is None
            or domain_key in seen_domain_keys
        ):
            raise ValueError("invalid_semantic_domain_key")
        if status not in _SEMANTIC_DOMAIN_STATUSES:
            raise ValueError("invalid_semantic_domain_status")
        seen_domain_keys.add(domain_key)
        counts = {
            field: _non_negative_int(domain[field], field_name=field)
            for field in domain_fields
            if field not in {"domain_key", "status"}
        }
        if counts["source_file_count"] <= 0 or (counts["semantic_file_count"] > counts["source_file_count"]):
            raise ValueError("invalid_semantic_domain_file_counts")
        if (
            counts["semantic_node_count"] > MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION
            or counts["semantic_edge_count"] > MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION
            or counts["semantic_node_bytes"] > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
            or counts["semantic_edge_bytes"] > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
            or counts["graph_declaration_count"] > MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION
            or counts["graph_declaration_bytes"] > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
        ):
            raise ValueError("invalid_semantic_domain_partition_budget")
        if status != "materialized" and any(
            counts[field]
            for field in (
                "semantic_file_count",
                "semantic_node_count",
                "semantic_edge_count",
                "semantic_node_bytes",
                "semantic_edge_bytes",
                "graph_declaration_count",
                "graph_declaration_bytes",
                "unresolved_edge_count",
            )
        ):
            raise ValueError("invalid_omitted_semantic_domain_evidence")
        if status == "no_semantic_records" and any(
            counts[field] for field in ("truncated_node_count", "truncated_edge_count")
        ):
            raise ValueError("invalid_empty_semantic_domain_evidence")
        if status == "materialized":
            materialized_evidence_count += 1
            materialized_node_bytes += counts["semantic_node_bytes"]
            materialized_edge_bytes += counts["semantic_edge_bytes"]
            materialized_declaration_bytes += counts["graph_declaration_bytes"]
        if counts["truncated_graph_declaration_count"] > counts["truncated_edge_count"]:
            raise ValueError("invalid_semantic_domain_declaration_truncation")
        evidenced_truncated_nodes += counts["truncated_node_count"]
        evidenced_truncated_edges += counts["truncated_edge_count"]
        if status == "materialized":
            evidenced_unresolved_edges += counts["unresolved_edge_count"]
        normalized_domains.append({"domain_key": domain_key, "status": status, **counts})
    if materialized_evidence_count != materialized_count:
        raise ValueError("semantic_materialized_domain_evidence_missing")
    if (
        materialized_node_bytes != semantic_node_bytes
        or materialized_edge_bytes != semantic_edge_bytes
        or materialized_declaration_bytes != graph_declaration_bytes
    ):
        raise ValueError("semantic_domain_byte_evidence_mismatch")
    if evidenced_truncated_nodes > truncated_node_count or evidenced_truncated_edges > truncated_edge_count:
        raise ValueError("semantic_domain_truncation_evidence_mismatch")
    if evidenced_unresolved_edges != unresolved_edge_count:
        raise ValueError("semantic_domain_unresolved_evidence_mismatch")
    return {
        "strategy": _SEMANTIC_DOMAIN_ADMISSION_STRATEGY,
        "top_level_domain_count": domain_count,
        "materialized_domain_count": materialized_count,
        "omitted_domain_count": omitted_count,
        "empty_domain_count": empty_count,
        "partition_count": partition_count,
        "evidence_count": evidence_count,
        "evidence_truncated_count": evidence_truncated_count,
        "max_partitions": max_partitions,
        "max_total_bytes": max_total_bytes,
        "aggregate_scope": "semantic_and_declaration_jsonl",
        "graph_declaration_bytes": graph_declaration_bytes,
        "final_graph_artifact_max_bytes": final_graph_max_bytes,
        "final_materializer_fail_closed": True,
        "domains": normalized_domains,
    }


def normalize_semantic_budget(
    budget: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if budget is None:
        return None
    if not isinstance(budget, Mapping):
        raise ValueError("invalid_semantic_budget")
    raw = dict(budget)
    required_fields = {
        "configured_max_records_per_partition",
        "max_records_per_partition",
        "max_bytes_per_partition",
        "configuration_clamped",
        "truncated",
        "truncated_node_count",
        "truncated_edge_count",
        "unresolved_edge_count",
        "semantic_node_bytes",
        "semantic_edge_bytes",
    }
    candidate_fields = {
        "candidate_edge_record_limit",
        "candidate_edge_byte_limit",
        "candidate_edge_count",
        "candidate_edge_bytes",
        "truncated_candidate_edge_count",
    }
    additive_fields = {"domain_admission"}
    if not required_fields.issubset(raw) or set(raw) - (required_fields | candidate_fields | additive_fields):
        raise ValueError("invalid_semantic_budget_fields")
    configured_limit = _non_negative_int(
        raw["configured_max_records_per_partition"],
        field_name="configured_max_records_per_partition",
    )
    effective_limit = _non_negative_int(
        raw["max_records_per_partition"],
        field_name="max_records_per_partition",
    )
    max_bytes = _non_negative_int(
        raw["max_bytes_per_partition"],
        field_name="max_bytes_per_partition",
    )
    if configured_limit <= 0 or effective_limit <= 0 or max_bytes <= 0:
        raise ValueError("invalid_semantic_budget_limit")
    if effective_limit > MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION or effective_limit > configured_limit:
        raise ValueError("invalid_semantic_budget_record_limit")
    if max_bytes > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION:
        raise ValueError("invalid_semantic_budget_byte_limit")
    normalized = {
        "configured_max_records_per_partition": configured_limit,
        "max_records_per_partition": effective_limit,
        "max_bytes_per_partition": max_bytes,
        "configuration_clamped": _strict_bool(
            raw["configuration_clamped"],
            field_name="configuration_clamped",
        ),
        "truncated": _strict_bool(
            raw["truncated"],
            field_name="truncated",
        ),
        "truncated_node_count": _non_negative_int(
            raw["truncated_node_count"],
            field_name="truncated_node_count",
        ),
        "truncated_edge_count": _non_negative_int(
            raw["truncated_edge_count"],
            field_name="truncated_edge_count",
        ),
        "unresolved_edge_count": _non_negative_int(
            raw["unresolved_edge_count"],
            field_name="unresolved_edge_count",
        ),
        "semantic_node_bytes": _non_negative_int(
            raw["semantic_node_bytes"],
            field_name="semantic_node_bytes",
        ),
        "semantic_edge_bytes": _non_negative_int(
            raw["semantic_edge_bytes"],
            field_name="semantic_edge_bytes",
        ),
    }
    if normalized["configuration_clamped"] != (configured_limit != effective_limit):
        raise ValueError("invalid_semantic_budget_clamp_state")
    if "domain_admission" not in raw:
        if normalized["semantic_node_bytes"] > max_bytes:
            raise ValueError("semantic_node_byte_budget_exceeded")
        if normalized["semantic_edge_bytes"] > max_bytes:
            raise ValueError("semantic_edge_byte_budget_exceeded")
    truncated = bool(normalized["truncated_node_count"] or normalized["truncated_edge_count"])
    if normalized["truncated"] != truncated:
        raise ValueError("invalid_semantic_budget_truncation_state")
    present_candidate_fields = {field for field in candidate_fields if field in raw}
    if present_candidate_fields:
        if present_candidate_fields != set(candidate_fields):
            raise ValueError("invalid_semantic_budget_candidate_fields")
        candidate_record_limit = _non_negative_int(
            raw.get("candidate_edge_record_limit"),
            field_name="candidate_edge_record_limit",
        )
        candidate_byte_limit = _non_negative_int(
            raw.get("candidate_edge_byte_limit"),
            field_name="candidate_edge_byte_limit",
        )
        candidate_count = _non_negative_int(
            raw.get("candidate_edge_count"),
            field_name="candidate_edge_count",
        )
        candidate_bytes = _non_negative_int(
            raw.get("candidate_edge_bytes"),
            field_name="candidate_edge_bytes",
        )
        truncated_candidate_count = _non_negative_int(
            raw.get("truncated_candidate_edge_count"),
            field_name="truncated_candidate_edge_count",
        )
        if (
            candidate_record_limit <= 0
            or candidate_record_limit > MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES
            or candidate_count > candidate_record_limit
        ):
            raise ValueError("invalid_semantic_budget_candidate_record_limit")
        if (
            candidate_byte_limit <= 0
            or candidate_byte_limit > MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES
            or candidate_bytes > candidate_byte_limit
        ):
            raise ValueError("invalid_semantic_budget_candidate_byte_limit")
        if truncated_candidate_count > normalized["truncated_edge_count"]:
            raise ValueError("invalid_semantic_budget_candidate_truncation")
        normalized.update(
            {
                "candidate_edge_record_limit": candidate_record_limit,
                "candidate_edge_byte_limit": candidate_byte_limit,
                "candidate_edge_count": candidate_count,
                "candidate_edge_bytes": candidate_bytes,
                "truncated_candidate_edge_count": truncated_candidate_count,
            }
        )
    if "domain_admission" in raw:
        normalized["domain_admission"] = _normalize_semantic_domain_admission(
            raw["domain_admission"],
            semantic_node_bytes=normalized["semantic_node_bytes"],
            semantic_edge_bytes=normalized["semantic_edge_bytes"],
            truncated_node_count=normalized["truncated_node_count"],
            truncated_edge_count=normalized["truncated_edge_count"],
            unresolved_edge_count=normalized["unresolved_edge_count"],
        )
    return normalized
