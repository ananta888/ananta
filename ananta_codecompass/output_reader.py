from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
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
    "index":       ["path", "file", "relative_path", "source"],
    "details":     ["file", "path", "relative_path", "source"],
    "context":     ["file", "path", "relative_path", "source", "context_file"],
    "embedding":   ["path", "file", "relative_path", "source"],
    "relations":   ["file", "source_name", "path", "from_path"],
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
        )
        if key in payload
    }


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
                "diagnostic_codes": sorted(
                    {str(value).strip() for value in diagnostic_codes if str(value).strip()}
                ),
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
    if not required_fields.issubset(raw) or set(raw) - (
        required_fields | candidate_fields
    ):
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
    if (
        effective_limit > MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION
        or effective_limit > configured_limit
    ):
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
    if normalized["semantic_node_bytes"] > max_bytes:
        raise ValueError("semantic_node_byte_budget_exceeded")
    if normalized["semantic_edge_bytes"] > max_bytes:
        raise ValueError("semantic_edge_byte_budget_exceeded")
    truncated = bool(
        normalized["truncated_node_count"]
        or normalized["truncated_edge_count"]
    )
    if normalized["truncated"] != truncated:
        raise ValueError("invalid_semantic_budget_truncation_state")
    present_candidate_fields = {
        field for field in candidate_fields if field in raw
    }
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
            or candidate_byte_limit
            > MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES
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
    return normalized


def _validate_semantic_partition_evidence(
    *,
    directory: Path,
    outputs: Mapping[str, Mapping[str, Any] | None],
    semantic_budget: Mapping[str, Any] | None,
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
        entry = outputs.get(output_kind)
        if not isinstance(entry, Mapping):
            if semantic_budget is not None:
                raise ValueError("semantic_partition_evidence_missing")
            continue
        path = directory / OUTPUT_FILENAME_BY_KEY[output_kind]
        try:
            actual_bytes = path.stat().st_size
        except OSError as exc:
            raise ValueError("semantic_partition_evidence_missing") from exc
        if int(entry["record_count"]) > max_records:
            raise ValueError("semantic_partition_record_budget_exceeded")
        if actual_bytes > max_bytes:
            raise ValueError("semantic_partition_byte_budget_exceeded")
        if (
            semantic_budget is not None
            and actual_bytes != int(semantic_budget[byte_field])
        ):
            raise ValueError("semantic_partition_byte_evidence_mismatch")


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
    output_kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    directory = Path(output_dir).resolve()
    selected_output_kinds = tuple(
        output_kinds if output_kinds is not None else OUTPUT_FILENAME_BY_KEY
    )
    if set(selected_output_kinds) - set(OUTPUT_FILENAME_BY_KEY):
        raise ValueError("unknown_codecompass_output_kind")
    if not _REQUIRED_OUTPUT_KEYS.issubset(selected_output_kinds):
        raise ValueError("required_codecompass_output_kind_missing")
    outputs: dict[str, dict[str, Any] | None] = {}
    for key in selected_output_kinds:
        filename = OUTPUT_FILENAME_BY_KEY[key]
        file_path = directory / filename
        if (
            key in {"semantic_nodes", "semantic_edges"}
            and file_path.exists()
            and file_path.stat().st_size
            > MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
        ):
            raise ValueError("semantic_partition_byte_budget_exceeded")
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
    normalized_registry = normalize_file_type_registry(file_type_registry)
    normalized_coverage = normalize_coverage(coverage)
    normalized_capabilities = normalize_file_type_capabilities(file_type_capabilities)
    normalized_semantic_budget = normalize_semantic_budget(semantic_budget)
    _validate_semantic_partition_evidence(
        directory=directory,
        outputs=outputs,
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
        selected_output_kinds = tuple(
            record_output_kinds
            if record_output_kinds is not None
            else _DEFAULT_RECORD_OUTPUT_KEYS
        )
        unknown_output_kinds = set(selected_output_kinds) - set(OUTPUT_FILENAME_BY_KEY)
        if unknown_output_kinds:
            raise ValueError("unknown_codecompass_output_kind")
        semantic_manifest_kinds = (
            ("semantic_nodes", "semantic_edges")
            if semantic_budget is not None
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
            output_kinds=manifest_output_kinds,
        )
        records: list[dict[str, Any]] = []
        malformed_total = 0
        skipped_total = 0
        missing_outputs: list[str] = []
        for key in selected_output_kinds:
            filename = OUTPUT_FILENAME_BY_KEY[key]
            file_path = directory / filename
            if not file_path.exists():
                if key in _REQUIRED_OUTPUT_KEYS:
                    missing_outputs.append(key)
                continue
            loaded_records, malformed, skipped = _iter_jsonl_records(file_path)
            malformed_total += malformed
            skipped_total += skipped
            for index, record in enumerate(loaded_records, start=1):
                records.append(
                    {
                        **record,
                        "_provenance": {
                            "engine": "codecompass_output_reader",
                            "record_id": str(record.get("id") or f"{key}:{index}"),
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
