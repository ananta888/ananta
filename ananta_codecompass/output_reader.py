"""CodeCompass output reader: manifest construction and provenance-tagged record loading.

Public entry point of the output-reading subsystem. File discovery, evidence
normalization and semantic-partition validation live in focused sibling
modules; their names stay importable from here for compatibility.
"""

from __future__ import annotations

import hashlib
import json
import re  # noqa: F401 - historic export of this module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath  # noqa: F401 - historic export of this module
from typing import Any

from ananta_codecompass.output_capability_normalization import (  # noqa: F401 - compatibility re-exports
    _CAPABILITY_MODES,
    _non_negative_int,
    _normalize_capability_claim,
    _strict_bool,
    normalize_coverage,
    normalize_file_type_capabilities,
    normalize_file_type_registry,
)
from ananta_codecompass.output_files import (  # noqa: F401 - compatibility re-exports
    _DEFAULT_RECORD_OUTPUT_KEYS,
    _MAX_EXISTING_MANIFEST_BYTES,
    _REQUIRED_OUTPUT_KEYS,
    _SEMANTIC_DOMAIN_SHARD_PATTERN,
    _SEMANTIC_OUTPUT_KEYS,
    OUTPUT_FILENAME_BY_KEY,
    _file_sha256,
    _iter_jsonl_records,
    _load_existing_manifest_evidence,
    _normalize_output_entry,
    _semantic_partition_metadata_present,
    _semantic_partition_paths,
    _SemanticPartitionPathSet,
    _validate_declared_partition_file_evidence,
)
from ananta_codecompass.output_semantic_budget import (  # noqa: F401 - compatibility re-exports
    _SEMANTIC_DOMAIN_ADMISSION_STRATEGY,
    _SEMANTIC_DOMAIN_STATUSES,
    _normalize_semantic_domain_admission,
    normalize_semantic_budget,
)
from ananta_codecompass.output_semantic_evidence import (  # noqa: F401 - compatibility re-exports
    _canonical_jsonl_record_bytes,
    _source_file_domain_key,
    _validate_graph_declaration_evidence,
    _validate_semantic_partition_evidence,
    _validate_semantic_record_domain_keys,
)
from ananta_contracts.codecompass_graph_limits import (  # noqa: F401 - historic export of this module
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (  # noqa: F401 - historic export of this module
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)

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
