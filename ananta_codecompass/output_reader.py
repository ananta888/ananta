from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
    codecompass_semantic_domain_key,
)

OUTPUT_FILENAME_BY_KEY = {
    "index": "index.jsonl",
    "details": "details.jsonl",
    "context": "context.jsonl",
    "embedding": "embedding.jsonl",
    "relations": "relations.jsonl",
    "graph_nodes": "graph_nodes.jsonl",
    "graph_edges": "graph_edges.jsonl",
    "semantic_nodes": "semantic_nodes.jsonl",
    "semantic_edges": "semantic_edges.jsonl",
}
_DEFAULT_RECORD_OUTPUT_KEYS = (
    "index",
    "details",
    "context",
    "embedding",
    "relations",
    "graph_nodes",
    "graph_edges",
)
_REQUIRED_OUTPUT_KEYS = frozenset(_DEFAULT_RECORD_OUTPUT_KEYS)

# CWFH-002: Canonical field priority for extracting the relative file path from each record type.
# First matching non-empty field wins.
_FILE_PATH_FIELD_PRIORITY: dict[str, list[str]] = {
    "index": ["path", "file", "relative_path", "source"],
    "details": ["file", "path", "relative_path", "source"],
    "context": ["file", "path", "relative_path", "source", "context_file"],
    "embedding": ["path", "file", "relative_path", "source"],
    "relations": ["file", "source_name", "path", "from_path"],
    "graph_nodes": ["file", "path", "source_path", "relative_path"],
    "graph_edges": ["source_path", "target_path", "path", "from_path"],
    "semantic_nodes": ["file", "path", "relative_path", "source"],
    "semantic_edges": ["source_path", "target_path", "path", "from_path"],
}

# Record kinds whose `path` field carries an XML/XPath node address
# ("/plugin", "/extension[0]/point") rather than a repo-relative file
# path. For these kinds, the `file` field is the real repo path and
# MUST be preferred over `path` even when `path` is non-empty.
_XML_NODE_KINDS = frozenset({"xml_node_detail", "xml_attribute", "xml_node"})


_DEFAULT_FILE_PATH_FIELDS = ["path", "file", "relative_path", "source"]

_CAPABILITY_MODES = {
    "indexed": frozenset({"none", "plain_text", "structured"}),
    "symbols": frozenset({"none", "heuristic", "parser_backed"}),
    "relationships": frozenset({"none", "structural", "referential", "semantic"}),
}

_MAX_EXISTING_MANIFEST_BYTES = 64 * 1024 * 1024
_SEMANTIC_OUTPUT_KEYS = frozenset({"semantic_nodes", "semantic_edges"})
_SEMANTIC_DOMAIN_SHARD_PATTERN = re.compile(r"^(semantic_nodes|semantic_edges)\.domain-[a-f0-9]{64}\.jsonl$")
_SEMANTIC_DOMAIN_ADMISSION_STRATEGY = "top_level_domain_bounded_admission_v1"
_REPOSITORY_ROOT_DOMAIN = "__repository_root__"
_SEMANTIC_DOMAIN_STATUSES = frozenset(
    {
        "materialized",
        "aggregate_byte_limit",
        "partition_limit",
        "per_partition_limit",
        "no_semantic_records",
    }
)


def extract_file_path_from_record(
    record: dict[str, Any],
    output_kind: str = "index",
) -> str | None:
    """
    CWFH-002: Extract the canonical relative file path from a CodeCompass output record.

    Returns the first non-empty value from the priority field list for the given output_kind,
    or None if no path can be determined. Never returns absolute paths (strips leading '/').

    For XML-node records (kind in {xml_node_detail, xml_attribute, xml_node})
    the `path` field carries an XPath like "/plugin" — not a repo path —
    so the `file` field is preferred over `path` for those kinds.
    """
    kind = record.get("kind")
    if kind in _XML_NODE_KINDS:
        # Force `file` first for XML-node records.
        fields = ["file", "relative_path", "source", "path"]
    else:
        fields = _FILE_PATH_FIELD_PRIORITY.get(output_kind, _DEFAULT_FILE_PATH_FIELDS)
    for field in fields:
        raw = record.get(field)
        if not raw:
            continue
        path = str(raw).strip()
        if not path:
            continue
        # Strip leading slash to ensure relative paths
        path = path.lstrip("/")
        if path:
            return path
    # Also try provenance
    prov = record.get("_provenance")
    if isinstance(prov, dict):
        raw = prov.get("file") or prov.get("path")
        if raw:
            return str(raw).strip().lstrip("/") or None
    return None


@dataclass(frozen=True)
class ReaderDiagnostics:
    malformed_line_count: int = 0
    skipped_non_object_count: int = 0
    missing_outputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "malformed_line_count": int(self.malformed_line_count),
            "skipped_non_object_count": int(self.skipped_non_object_count),
            "missing_outputs": list(self.missing_outputs),
        }


@dataclass(frozen=True)
class _SemanticPartitionPathSet:
    paths: tuple[Path, ...]
    declared_evidence: Mapping[str, Mapping[str, object]]


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 64), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _iter_jsonl_records(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    malformed = 0
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = line.strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(parsed, dict):
            skipped += 1
            continue
        records.append(parsed)
    return records, malformed, skipped


def _normalize_output_entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    records, malformed, skipped = _iter_jsonl_records(path)
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "mtime": float(stat.st_mtime),
        "record_count": len(records),
        "_records": records,
        "_malformed": malformed,
        "_skipped": skipped,
    }


def _load_existing_manifest_evidence(directory: Path) -> dict[str, Any]:
    """Read additive evidence from the one existing manifest, if present."""

    manifest_path = directory / "manifest.json"
    try:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            return {}
        if manifest_path.stat().st_size > _MAX_EXISTING_MANIFEST_BYTES:
            return {}
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key]
        for key in (
            "file_type_registry",
            "coverage",
            "file_type_capabilities",
            "semantic_budget",
            "partitioned_outputs",
        )
        if key in payload
    }


def _semantic_partition_paths(
    *,
    directory: Path,
    output_kind: str,
    partitioned_outputs: Mapping[str, object] | None,
) -> _SemanticPartitionPathSet:
    raw_paths = partitioned_outputs.get(output_kind) if isinstance(partitioned_outputs, Mapping) else None
    if raw_paths is None:
        if any(directory.glob(f"{output_kind}.domain-*.jsonl")):
            raise ValueError("semantic_partition_manifest_missing")
        return _SemanticPartitionPathSet(
            paths=(directory / OUTPUT_FILENAME_BY_KEY[output_kind],),
            declared_evidence={},
        )
    if (
        not isinstance(raw_paths, Sequence)
        or isinstance(raw_paths, (str, bytes))
        or not 1 <= len(raw_paths) <= MAX_CODECOMPASS_SEMANTIC_PARTITIONS
    ):
        raise ValueError("semantic_partition_manifest_invalid")
    expected_prefix = f"{output_kind}."
    expected_legacy = OUTPUT_FILENAME_BY_KEY[output_kind]
    normalized: set[str] = set()
    declared_evidence: dict[str, Mapping[str, object]] = {}
    declaration_mode: str | None = None
    for raw_declaration in raw_paths:
        if isinstance(raw_declaration, str):
            mode = "raw_path"
            raw_path = raw_declaration
        elif isinstance(raw_declaration, Mapping):
            mode = "output_file_evidence"
            evidence = dict(raw_declaration)
            if set(evidence) != {
                "path",
                "sha256",
                "mtime",
                "record_count",
            }:
                raise ValueError("semantic_partition_evidence_invalid")
            raw_path = evidence["path"]
            if not isinstance(raw_path, str):
                raise ValueError("semantic_partition_path_invalid")
        else:
            raise ValueError("semantic_partition_path_invalid")
        if declaration_mode is not None and declaration_mode != mode:
            raise ValueError("semantic_partition_manifest_mixed")
        declaration_mode = mode
        candidate = Path(raw_path)
        if candidate.is_absolute():
            name = candidate.name
            expected_path = directory / name
            try:
                if candidate.resolve() != expected_path.resolve():
                    raise ValueError("semantic_partition_path_invalid")
            except OSError as exc:
                raise ValueError("semantic_partition_path_invalid") from exc
        else:
            posix_candidate = PurePosixPath(raw_path)
            name = str(posix_candidate)
            if len(posix_candidate.parts) != 1:
                raise ValueError("semantic_partition_path_invalid")
        if (
            name in {"", ".", ".."}
            or not name.endswith(".jsonl")
            or (
                name != expected_legacy
                and (not name.startswith(expected_prefix) or _SEMANTIC_DOMAIN_SHARD_PATTERN.fullmatch(name) is None)
            )
        ):
            raise ValueError("semantic_partition_path_invalid")
        if name in normalized:
            raise ValueError("semantic_partition_path_duplicate")
        normalized.add(name)
        if mode == "output_file_evidence":
            declared_evidence[name] = evidence
    paths = tuple(directory / name for name in sorted(normalized))
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("semantic_partition_file_missing")
    actual_names = {candidate.name for candidate in directory.glob(f"{output_kind}.domain-*.jsonl")}
    legacy_path = directory / expected_legacy
    if legacy_path.is_file() or legacy_path.is_symlink():
        actual_names.add(expected_legacy)
    if actual_names != normalized:
        raise ValueError("semantic_partition_manifest_mismatch")
    return _SemanticPartitionPathSet(
        paths=paths,
        declared_evidence=declared_evidence,
    )


def _validate_declared_partition_file_evidence(
    *,
    actual: Mapping[str, Any],
    declared: Mapping[str, object] | None,
) -> None:
    if declared is None:
        return
    declared_hash = declared.get("sha256")
    declared_count = declared.get("record_count")
    declared_mtime = declared.get("mtime")
    if not isinstance(declared_hash, str) or re.fullmatch(r"[a-f0-9]{64}", declared_hash) is None:
        raise ValueError("semantic_partition_hash_evidence_invalid")
    if declared_hash != actual["sha256"]:
        raise ValueError("semantic_partition_hash_evidence_mismatch")
    if isinstance(declared_count, bool) or not isinstance(declared_count, int) or declared_count < 0:
        raise ValueError("semantic_partition_count_evidence_invalid")
    if declared_count != actual["record_count"]:
        raise ValueError("semantic_partition_count_evidence_mismatch")
    if isinstance(declared_mtime, bool) or not isinstance(declared_mtime, (int, float)):
        raise ValueError("semantic_partition_mtime_evidence_invalid")
    if float(declared_mtime) != float(actual["mtime"]):
        raise ValueError("semantic_partition_mtime_evidence_mismatch")


def _semantic_partition_metadata_present(
    partitioned_outputs: Mapping[str, object] | None,
) -> bool:
    if not isinstance(partitioned_outputs, Mapping):
        return False
    present = {key for key in _SEMANTIC_OUTPUT_KEYS if key in partitioned_outputs}
    if present and present != _SEMANTIC_OUTPUT_KEYS:
        raise ValueError("semantic_partition_manifest_incomplete")
    return bool(present)


def _normalize_capability_claim(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    claim = dict(raw or {})
    effective = str(claim.get("effective") or "none").strip().lower() or "none"
    if effective not in _CAPABILITY_MODES[name]:
        raise ValueError(f"invalid_{name}_effective:{effective}")
    configured = bool(claim.get("configured", False))
    runtime_available = bool(claim.get("runtime_available", False))
    verified = bool(claim.get("verified", False))
    if verified and not configured:
        raise ValueError(f"invalid_{name}_verified_claim")
    if effective != "none" and not (configured and runtime_available and verified):
        raise ValueError(f"invalid_{name}_effective_claim")
    return {
        "configured": configured,
        "runtime_available": runtime_available,
        "verified": verified,
        "effective": effective,
    }


def normalize_file_type_capabilities(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate and deterministically project aggregate file-type capabilities.

    This is an additive view on the canonical VPA output manifest. It does not
    enumerate files or authorize paths. Duplicate type/pipeline aggregates are
    rejected so one run cannot publish competing capability truths.
    """

    raw_entries = list(entries or [])
    if len(raw_entries) > 1_024:
        raise ValueError("file_type_capability_limit_exceeded")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("invalid_file_type_capability")
        detected_type = str(raw.get("detected_type") or "").strip()
        pipeline = str(raw.get("pipeline") or "").strip()
        if not detected_type or not pipeline:
            raise ValueError("missing_file_type_capability_identity")
        identity = (detected_type, pipeline)
        if identity in seen:
            raise ValueError(f"duplicate_file_type_capability:{detected_type}:{pipeline}")
        seen.add(identity)
        claims = {
            name: _normalize_capability_claim(name, dict(raw.get(name) or {}))
            for name in ("indexed", "symbols", "relationships")
        }
        if claims["symbols"]["effective"] != "none" and claims["indexed"]["effective"] == "none":
            raise ValueError("symbols_require_indexed")
        if claims["relationships"]["effective"] != "none" and claims["symbols"]["effective"] == "none":
            raise ValueError("relationships_require_symbols")
        diagnostic_codes = list(raw.get("diagnostic_codes") or [])
        if len(diagnostic_codes) > 256:
            raise ValueError("file_type_diagnostic_limit_exceeded")
        normalized.append(
            {
                "detected_type": detected_type,
                "pipeline": pipeline,
                **claims,
                "parser_id": str(raw.get("parser_id") or "").strip() or None,
                "parser_version": str(raw.get("parser_version") or "").strip() or None,
                "fallback_reason": str(raw.get("fallback_reason") or "").strip() or None,
                "diagnostic_codes": sorted({str(value).strip() for value in diagnostic_codes if str(value).strip()}),
                "file_count": _non_negative_int(raw.get("file_count"), field_name="file_count"),
            }
        )
    return sorted(normalized, key=lambda item: (item["detected_type"], item["pipeline"]))


def normalize_coverage(coverage: dict[str, Any] | None) -> dict[str, Any] | None:
    if coverage is None:
        return None
    raw = dict(coverage or {})
    counts = {
        key: _non_negative_int(raw.get(key), field_name=key)
        for key in ("manifest_candidate_count", "indexed", "excluded", "unsupported", "failed")
    }
    classified = counts["indexed"] + counts["excluded"] + counts["unsupported"] + counts["failed"]
    if classified != counts["manifest_candidate_count"]:
        raise ValueError("coverage_count_mismatch")
    diagnostics = {
        str(key): _non_negative_int(value, field_name="diagnostic_count")
        for key, value in dict(raw.get("diagnostic_counts") or {}).items()
        if str(key).strip()
    }
    return {
        **counts,
        "truncated": bool(raw.get("truncated", False)),
        "diagnostic_counts": dict(sorted(diagnostics.items())),
    }


def normalize_file_type_registry(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    if metadata is None:
        return None
    raw = dict(metadata or {})
    normalized = {
        "schema_version": str(raw.get("schema_version") or "").strip(),
        "registry_version": str(raw.get("registry_version") or "").strip(),
        "snapshot_hash": str(raw.get("snapshot_hash") or "").strip().lower(),
    }
    if not normalized["schema_version"] or not normalized["registry_version"]:
        raise ValueError("invalid_file_type_registry_version")
    if len(normalized["snapshot_hash"]) != 64 or any(
        char not in "0123456789abcdef" for char in normalized["snapshot_hash"]
    ):
        raise ValueError("invalid_file_type_registry_snapshot_hash")
    return normalized


def _non_negative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid_{field_name}")
    return value


def _strict_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"invalid_{field_name}")
    return value


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
    domain = head if separator and head else _REPOSITORY_ROOT_DOMAIN
    return codecompass_semantic_domain_key(domain)


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


def build_output_manifest(
    *,
    output_dir: str | Path,
    codecompass_version: str = "unknown",
    profile_name: str = "default",
    source_scope: str = "repo",
    generated_at: str = "unknown",
    file_type_registry: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    file_type_capabilities: list[dict[str, Any]] | None = None,
    semantic_budget: dict[str, Any] | None = None,
    partitioned_outputs: Mapping[str, object] | None = None,
    output_kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    directory = Path(output_dir).resolve()
    if partitioned_outputs is not None and not isinstance(
        partitioned_outputs,
        Mapping,
    ):
        raise ValueError("semantic_partition_manifest_invalid")
    partition_metadata_present = _semantic_partition_metadata_present(partitioned_outputs)
    selected_output_kinds = tuple(output_kinds if output_kinds is not None else OUTPUT_FILENAME_BY_KEY)
    if set(selected_output_kinds) - set(OUTPUT_FILENAME_BY_KEY):
        raise ValueError("unknown_codecompass_output_kind")
    if not _REQUIRED_OUTPUT_KEYS.issubset(selected_output_kinds):
        raise ValueError("required_codecompass_output_kind_missing")
    if partition_metadata_present and not _SEMANTIC_OUTPUT_KEYS.issubset(selected_output_kinds):
        raise ValueError("semantic_partition_output_kind_missing")
    outputs: dict[str, dict[str, Any] | None] = {}
    semantic_partitions: dict[str, list[dict[str, Any]]] = {}
    for key in selected_output_kinds:
        if key in _SEMANTIC_OUTPUT_KEYS:
            path_set = _semantic_partition_paths(
                directory=directory,
                output_kind=key,
                partitioned_outputs=partitioned_outputs,
            )
            entries: list[dict[str, Any]] = []
            for file_path in path_set.paths:
                if not file_path.exists():
                    continue
                if file_path.stat().st_size > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION:
                    raise ValueError("semantic_partition_byte_budget_exceeded")
                entry = _normalize_output_entry(file_path)
                _validate_declared_partition_file_evidence(
                    actual=entry,
                    declared=path_set.declared_evidence.get(file_path.name),
                )
                entries.append(entry)
            semantic_partitions[key] = entries
            legacy_path = directory / OUTPUT_FILENAME_BY_KEY[key]
            outputs[key] = entries[0] if len(entries) == 1 and Path(str(entries[0]["path"])) == legacy_path else None
            continue
        filename = OUTPUT_FILENAME_BY_KEY[key]
        file_path = directory / filename
        outputs[key] = _normalize_output_entry(file_path) if file_path.exists() else None
    manifest = {
        "schema": "codecompass_output_manifest.v1",
        "codecompass_version": str(codecompass_version or "unknown").strip() or "unknown",
        "profile_name": str(profile_name or "default").strip() or "default",
        "source_scope": str(source_scope or "repo").strip() or "repo",
        "generated_at": str(generated_at or "unknown").strip() or "unknown",
        "output_dir": str(directory),
        "outputs": {
            key: (
                {
                    "path": value["path"],
                    "sha256": value["sha256"],
                    "mtime": value["mtime"],
                    "record_count": value["record_count"],
                }
                if value is not None
                else None
            )
            for key, value in outputs.items()
        },
    }
    if partition_metadata_present:
        manifest["partitioned_outputs"] = {
            key: [
                {
                    "path": entry["path"],
                    "sha256": entry["sha256"],
                    "mtime": entry["mtime"],
                    "record_count": entry["record_count"],
                }
                for entry in semantic_partitions.get(key, [])
            ]
            for key in sorted(_SEMANTIC_OUTPUT_KEYS)
            if key in selected_output_kinds
        }
    normalized_registry = normalize_file_type_registry(file_type_registry)
    normalized_coverage = normalize_coverage(coverage)
    normalized_capabilities = normalize_file_type_capabilities(file_type_capabilities)
    normalized_semantic_budget = normalize_semantic_budget(semantic_budget)
    _validate_semantic_partition_evidence(
        partitions=semantic_partitions,
        semantic_budget=normalized_semantic_budget,
        partition_metadata_present=partition_metadata_present,
    )
    _validate_graph_declaration_evidence(
        graph_nodes=outputs.get("graph_nodes"),
        graph_edges=outputs.get("graph_edges"),
        semantic_budget=normalized_semantic_budget,
    )
    if normalized_registry is not None:
        manifest["file_type_registry"] = normalized_registry
    if normalized_coverage is not None:
        manifest["coverage"] = normalized_coverage
    if file_type_capabilities is not None:
        manifest["file_type_capabilities"] = normalized_capabilities
    if normalized_semantic_budget is not None:
        manifest["semantic_budget"] = normalized_semantic_budget
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    manifest["manifest_hash"] = manifest_hash
    return manifest


class CodeCompassOutputReader:
    def load_from_output_dir(
        self,
        *,
        output_dir: str | Path,
        codecompass_version: str = "unknown",
        profile_name: str = "default",
        source_scope: str = "repo",
        generated_at: str = "unknown",
        file_type_registry: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
        file_type_capabilities: list[dict[str, Any]] | None = None,
        semantic_budget: dict[str, Any] | None = None,
        record_output_kinds: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        directory = Path(output_dir).resolve()
        existing_evidence = _load_existing_manifest_evidence(directory)
        if file_type_registry is None:
            file_type_registry = existing_evidence.get("file_type_registry")
        if coverage is None:
            coverage = existing_evidence.get("coverage")
        if file_type_capabilities is None and "file_type_capabilities" in existing_evidence:
            file_type_capabilities = existing_evidence["file_type_capabilities"]
        if semantic_budget is None:
            semantic_budget = existing_evidence.get("semantic_budget")
        partitioned_outputs = existing_evidence.get("partitioned_outputs")
        if partitioned_outputs is not None and not isinstance(
            partitioned_outputs,
            Mapping,
        ):
            raise ValueError("semantic_partition_manifest_invalid")
        selected_output_kinds = tuple(
            record_output_kinds if record_output_kinds is not None else _DEFAULT_RECORD_OUTPUT_KEYS
        )
        unknown_output_kinds = set(selected_output_kinds) - set(OUTPUT_FILENAME_BY_KEY)
        if unknown_output_kinds:
            raise ValueError("unknown_codecompass_output_kind")
        semantic_manifest_kinds = (
            ("semantic_nodes", "semantic_edges")
            if semantic_budget is not None or _semantic_partition_metadata_present(partitioned_outputs)
            else ()
        )
        manifest_output_kinds = tuple(
            dict.fromkeys(
                [
                    *_DEFAULT_RECORD_OUTPUT_KEYS,
                    *selected_output_kinds,
                    *semantic_manifest_kinds,
                ]
            )
        )
        manifest = build_output_manifest(
            output_dir=directory,
            codecompass_version=codecompass_version,
            profile_name=profile_name,
            source_scope=source_scope,
            generated_at=generated_at,
            file_type_registry=file_type_registry,
            coverage=coverage,
            file_type_capabilities=file_type_capabilities,
            semantic_budget=semantic_budget,
            partitioned_outputs=partitioned_outputs,
            output_kinds=manifest_output_kinds,
        )
        records: list[dict[str, Any]] = []
        malformed_total = 0
        skipped_total = 0
        missing_outputs: list[str] = []
        for key in selected_output_kinds:
            file_paths = (
                _semantic_partition_paths(
                    directory=directory,
                    output_kind=key,
                    partitioned_outputs=partitioned_outputs,
                ).paths
                if key in _SEMANTIC_OUTPUT_KEYS
                else (directory / OUTPUT_FILENAME_BY_KEY[key],)
            )
            existing_paths = tuple(file_path for file_path in file_paths if file_path.exists())
            if not existing_paths:
                if key in _REQUIRED_OUTPUT_KEYS:
                    missing_outputs.append(key)
                continue
            record_ordinal = 0
            for file_path in existing_paths:
                loaded_records, malformed, skipped = _iter_jsonl_records(file_path)
                malformed_total += malformed
                skipped_total += skipped
                for record in loaded_records:
                    record_ordinal += 1
                    records.append(
                        {
                            **record,
                            "_provenance": {
                                "engine": "codecompass_output_reader",
                                "record_id": str(record.get("id") or f"{key}:{record_ordinal}"),
                                "output_kind": key,
                                "output_file": str(file_path),
                                "manifest_hash": str(manifest.get("manifest_hash") or ""),
                                "source_scope": str(source_scope or "repo"),
                            },
                        }
                    )
        diagnostics = ReaderDiagnostics(
            malformed_line_count=malformed_total,
            skipped_non_object_count=skipped_total,
            missing_outputs=tuple(sorted(missing_outputs)),
        )
        return {
            "manifest": manifest,
            "records": records,
            "diagnostics": diagnostics.as_dict(),
            "standalone_compatible": True,
        }
