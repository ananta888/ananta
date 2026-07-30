from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTPUT_FILENAME_BY_KEY = {
    "index": "index.jsonl",
    "details": "details.jsonl",
    "context": "context.jsonl",
    "embedding": "embedding.jsonl",
    "relations": "relations.jsonl",
    "graph_nodes": "graph_nodes.jsonl",
    "graph_edges": "graph_edges.jsonl",
}

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


def _load_existing_file_type_evidence(directory: Path) -> dict[str, Any]:
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
        for key in ("file_type_registry", "coverage", "file_type_capabilities")
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
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid_{field_name}") from exc
    if normalized < 0:
        raise ValueError(f"invalid_{field_name}")
    return normalized


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
) -> dict[str, Any]:
    directory = Path(output_dir).resolve()
    outputs: dict[str, dict[str, Any] | None] = {}
    for key, filename in OUTPUT_FILENAME_BY_KEY.items():
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
    normalized_registry = normalize_file_type_registry(file_type_registry)
    normalized_coverage = normalize_coverage(coverage)
    normalized_capabilities = normalize_file_type_capabilities(file_type_capabilities)
    if normalized_registry is not None:
        manifest["file_type_registry"] = normalized_registry
    if normalized_coverage is not None:
        manifest["coverage"] = normalized_coverage
    if file_type_capabilities is not None:
        manifest["file_type_capabilities"] = normalized_capabilities
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
    ) -> dict[str, Any]:
        directory = Path(output_dir).resolve()
        existing_evidence = _load_existing_file_type_evidence(directory)
        if file_type_registry is None:
            file_type_registry = existing_evidence.get("file_type_registry")
        if coverage is None:
            coverage = existing_evidence.get("coverage")
        if file_type_capabilities is None and "file_type_capabilities" in existing_evidence:
            file_type_capabilities = existing_evidence["file_type_capabilities"]
        manifest = build_output_manifest(
            output_dir=directory,
            codecompass_version=codecompass_version,
            profile_name=profile_name,
            source_scope=source_scope,
            generated_at=generated_at,
            file_type_registry=file_type_registry,
            coverage=coverage,
            file_type_capabilities=file_type_capabilities,
        )
        records: list[dict[str, Any]] = []
        malformed_total = 0
        skipped_total = 0
        missing_outputs: list[str] = []
        for key, filename in OUTPUT_FILENAME_BY_KEY.items():
            file_path = directory / filename
            if not file_path.exists():
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
