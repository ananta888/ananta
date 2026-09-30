"""CodeCompass output file layout, semantic partition discovery and JSONL loading."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ananta_contracts.codecompass_graph_limits import MAX_CODECOMPASS_SEMANTIC_PARTITIONS

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

_MAX_EXISTING_MANIFEST_BYTES = 64 * 1024 * 1024
_SEMANTIC_OUTPUT_KEYS = frozenset({"semantic_nodes", "semantic_edges"})
_SEMANTIC_DOMAIN_SHARD_PATTERN = re.compile(r"^(semantic_nodes|semantic_edges)\.domain-[a-f0-9]{64}\.jsonl$")


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

