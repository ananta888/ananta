"""Worker-owned materialization of revision-bound CodeCompass graph artifacts.

The Hub delegates an index job and receives only published artifacts.  This
module turns the completed worker-local CodeCompass output directory into the
two graph artifacts consumed by the Hub.  It deliberately has no task-queue or
Hub-persistence dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from worker.retrieval.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
    WorkerCodeCompassDomainSupplementMaterializer,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_graph_visual_metrics import (
    materialize_graph_visual_metrics,
    verify_visual_metrics_content_hash,
)
from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader

GRAPH_INDEX_FILENAME = "cc_graph_index.json"
GRAPH_VISUAL_METRICS_FILENAME = "cc_graph_index.visual_metrics.json"
GRAPH_INDEX_SCHEMA = "codecompass_graph_index.v1"
GRAPH_VISUAL_OPTIONS_SCHEMA = "codecompass_graph_visual_options.v1"
MAX_CONFIGURED_BLAST_RADIUS_SEEDS = 256
MAX_SEED_ID_LENGTH = 512
MAX_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024

_GRAPH_OUTPUT_KINDS = frozenset(
    {
        "graph_nodes",
        "graph_edges",
        "semantic_nodes",
        "semantic_edges",
        "equivalence_rules",
        "translation_contracts",
        "transform_artifacts",
        "x86_nodes",
        "x86_edges",
        "rig_nodes",
        "rig_edges",
    }
)

_GRAPH_NODE_OUTPUT_KINDS = frozenset(
    {"graph_nodes", "semantic_nodes", "x86_nodes", "rig_nodes"}
)


class GraphArtifactExecutionDeadlinePort(Protocol):
    def checkpoint(self) -> None: ...


def _checkpoint(
    execution_deadline: GraphArtifactExecutionDeadlinePort | None,
) -> None:
    if execution_deadline is not None:
        execution_deadline.checkpoint()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def normalize_graph_visual_options(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the bounded, additive worker metric options.

    Advanced degree/bridge metrics default to enabled.  Their implementation
    has its own graph-size cap; configured blast-radius seeds are additionally
    input-bounded here before any algorithm runs.
    """

    if raw is not None and not isinstance(raw, Mapping):
        raise ValueError("graph_visual_options_invalid")
    options = dict(raw or {})
    allowed = {"schema", "include_advanced_metrics", "blast_radius_seeds"}
    if set(options) - allowed:
        raise ValueError("graph_visual_options_fields_unknown")
    schema = str(options.get("schema") or GRAPH_VISUAL_OPTIONS_SCHEMA)
    if schema != GRAPH_VISUAL_OPTIONS_SCHEMA:
        raise ValueError("graph_visual_options_schema_invalid")
    include_advanced = options.get("include_advanced_metrics", True)
    if not isinstance(include_advanced, bool):
        raise ValueError("graph_visual_options_advanced_metrics_invalid")
    raw_seeds = options.get("blast_radius_seeds", [])
    if not isinstance(raw_seeds, list) or len(raw_seeds) > MAX_CONFIGURED_BLAST_RADIUS_SEEDS:
        raise ValueError("graph_visual_options_blast_seeds_invalid")
    seeds: set[str] = set()
    for raw_seed in raw_seeds:
        if not isinstance(raw_seed, str):
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seed = raw_seed.strip()
        if not seed or len(seed) > MAX_SEED_ID_LENGTH:
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seeds.add(seed)
    return {
        "schema": GRAPH_VISUAL_OPTIONS_SCHEMA,
        "include_advanced_metrics": include_advanced,
        "blast_radius_seeds": sorted(seeds),
    }


def _sanitize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Drop worker-local path metadata before publishing a graph artifact."""

    sanitized = dict(record)
    provenance = sanitized.get("_provenance")
    if isinstance(provenance, Mapping):
        sanitized["_provenance"] = {
            key: value
            for key, value in provenance.items()
            if key not in {"output_file", "output_dir", "storage_path", "mtime"}
        }
    return sanitized


def _stable_graph_revision(
    records: list[dict[str, Any]],
    *,
    domain_supplement_content_hash: str | None = None,
) -> str:
    """Bind a revision to graph evidence, independent of container paths/time."""

    revision_records: list[dict[str, Any]] = []
    for record in records:
        provenance = record.get("_provenance")
        output_kind = (
            str(provenance.get("output_kind") or "").strip().lower()
            if isinstance(provenance, Mapping)
            else ""
        )
        if output_kind not in _GRAPH_OUTPUT_KINDS:
            continue
        normalized = _sanitize_record(record)
        normalized_provenance = dict(normalized.get("_provenance") or {})
        normalized_provenance.pop("manifest_hash", None)
        normalized["_provenance"] = normalized_provenance
        revision_records.append(normalized)
    revision_records.sort(key=_canonical_json)
    revision_source: dict[str, Any] = {
        "schema": "codecompass_graph_source_revision.v1",
        "records": revision_records,
    }
    if domain_supplement_content_hash is not None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", domain_supplement_content_hash):
            raise ValueError("codecompass_domain_supplement_content_hash_invalid")
        revision_source["domain_supplement_content_hash"] = (
            domain_supplement_content_hash
        )
    digest = hashlib.sha256(_canonical_json(revision_source)).hexdigest()
    return f"sha256:{digest}"


def _graph_export_required(
    knowledge_index: Mapping[str, Any],
    run: Mapping[str, Any],
) -> bool:
    for owner, metadata_field in (
        (run, "run_metadata"),
        (knowledge_index, "index_metadata"),
    ):
        metadata = owner.get(metadata_field)
        profile = metadata.get("profile") if isinstance(metadata, Mapping) else None
        limits = profile.get("limits") if isinstance(profile, Mapping) else None
        if isinstance(limits, Mapping) and "graph_export_mode" in limits:
            mode = str(limits.get("graph_export_mode") or "off").strip().lower()
            return mode not in {"", "off", "none"}
    return False


class WorkerCodeCompassGraphArtifactMaterializer:
    """Build both graph artifacts from one completed worker index output."""

    def __init__(
        self,
        *,
        max_graph_artifact_bytes: int = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
    ) -> None:
        normalized_limit = int(max_graph_artifact_bytes)
        if (
            normalized_limit <= 0
            or normalized_limit > MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES
        ):
            raise ValueError("codecompass_graph_artifact_limit_invalid")
        self._max_graph_artifact_bytes = normalized_limit

    def materialize(
        self,
        *,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: "GraphArtifactExecutionDeadlinePort | None" = None,
    ) -> dict[str, Any]:
        _checkpoint(execution_deadline)
        output_dir = self._resolve_output_dir(knowledge_index=knowledge_index, run=run)
        normalized_options = normalize_graph_visual_options(options)
        try:
            loaded = CodeCompassOutputReader().load_from_output_dir(
                output_dir=output_dir,
                profile_name=str(
                    run.get("profile_name")
                    or knowledge_index.get("profile_name")
                    or "default"
                ),
                source_scope=str(
                    knowledge_index.get("source_scope") or "artifact"
                ),
                generated_at="not_recorded",
                record_output_kinds=(
                    "graph_nodes",
                    "graph_edges",
                    "semantic_nodes",
                    "semantic_edges",
                ),
            )
            _checkpoint(execution_deadline)
        except ValueError as exc:
            raise RuntimeError("knowledge_index_graph_output_invalid") from exc
        diagnostics = loaded.get("diagnostics")
        if isinstance(diagnostics, Mapping) and (
            diagnostics.get("malformed_line_count", 0)
            or diagnostics.get("skipped_non_object_count", 0)
        ):
            raise RuntimeError("knowledge_index_graph_output_invalid")
        records = []
        for item in list(loaded.get("records") or []):
            _checkpoint(execution_deadline)
            if isinstance(item, Mapping):
                records.append(_sanitize_record(item))
        if _graph_export_required(knowledge_index, run) and not any(
            str((record.get("_provenance") or {}).get("output_kind") or "")
            .strip()
            .lower()
            in _GRAPH_NODE_OUTPUT_KINDS
            for record in records
        ):
            raise RuntimeError("knowledge_index_graph_output_empty")
        raw_manifest = loaded.get("manifest")
        manifest = raw_manifest if isinstance(raw_manifest, Mapping) else {}
        normalized_binding = self._normalize_revision_binding(
            knowledge_index=knowledge_index,
            manifest=(
                self._load_source_manifest(output_dir)
                if revision_binding is not None
                else manifest
            ),
            revision_binding=revision_binding,
        )
        supplement_materializer = WorkerCodeCompassDomainSupplementMaterializer()
        supplement_source_path = output_dir / DOMAIN_SUPPLEMENT_SOURCE_FILENAME
        supplement_content = None
        if normalized_binding is not None:
            if supplement_source_path.exists():
                supplement_content = supplement_materializer.inspect_source(
                    supplement_source_path,
                    execution_deadline=execution_deadline,
                )
            elif normalized_binding["source_scope"] == "repo_path":
                raise RuntimeError(
                    "knowledge_index_domain_supplement_source_missing"
                )
        revision = _stable_graph_revision(
            records,
            domain_supplement_content_hash=(
                supplement_content.logical_content_hash
                if supplement_content is not None
                else None
            ),
        )
        for record in records:
            _checkpoint(execution_deadline)
            provenance = record.get("_provenance")
            if isinstance(provenance, dict):
                provenance["manifest_hash"] = revision
        records.sort(key=_canonical_json)
        raw_semantic_budget = manifest.get("semantic_budget")
        semantic_budget = (
            dict(raw_semantic_budget)
            if isinstance(raw_semantic_budget, Mapping)
            else None
        )

        store = CodeCompassGraphStore(
            index_path=output_dir / GRAPH_INDEX_FILENAME,
            max_artifact_bytes=self._max_graph_artifact_bytes,
        )
        store.rebuild_from_output_records(
            records=records,
            manifest_hash=revision,
            semantic_budget=semantic_budget,
        )
        _checkpoint(execution_deadline)
        graph_index_path = output_dir / GRAPH_INDEX_FILENAME
        self._assert_admissible_graph_artifact(graph_index_path)
        metrics = materialize_graph_visual_metrics(
            graph_store=store,
            include_advanced_metrics=normalized_options["include_advanced_metrics"],
            blast_radius_seeds=normalized_options["blast_radius_seeds"],
        )
        _checkpoint(execution_deadline)
        visual_metrics_path = output_dir / GRAPH_VISUAL_METRICS_FILENAME
        self._assert_admissible_graph_artifact(visual_metrics_path)
        graph_payload = store.load()
        actual_revision = str((graph_payload.get("state") or {}).get("manifest_hash") or "")
        if actual_revision != revision or str(metrics.get("graph_revision") or "") != revision:
            raise RuntimeError("codecompass_graph_artifact_revision_mismatch")
        if not verify_visual_metrics_content_hash(metrics):
            raise RuntimeError("codecompass_graph_visual_metrics_hash_invalid")
        supplement_result: dict[str, Any] | None = None
        if supplement_content is not None and normalized_binding is not None:
            supplement_result = supplement_materializer.materialize(
                source_path=supplement_source_path,
                output_path=output_dir / DOMAIN_SUPPLEMENT_FILENAME,
                graph_revision=revision,
                source_scope=normalized_binding["source_scope"],
                knowledge_index_id=str(knowledge_index.get("id") or ""),
                source_id=normalized_binding["source_id"],
                source_revision_id=normalized_binding["source_revision_id"],
                source_revision_digest=normalized_binding[
                    "source_revision_digest"
                ],
                expected_content_hash=supplement_content.logical_content_hash,
                execution_deadline=execution_deadline,
            )
        _checkpoint(execution_deadline)
        result = {
            "schema": "codecompass_graph_artifact_materialization.v1",
            "graph_revision": revision,
            "graph_index_path": str(graph_index_path),
            "visual_metrics_path": str(visual_metrics_path),
            "visual_metrics_content_hash": str(metrics.get("content_hash") or ""),
            "options": normalized_options,
        }
        if supplement_result is not None:
            result["domain_supplement"] = supplement_result
        return result

    @staticmethod
    def _normalize_revision_binding(
        *,
        knowledge_index: Mapping[str, Any],
        manifest: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None,
    ) -> dict[str, str] | None:
        if revision_binding is None:
            return None
        if not isinstance(revision_binding, Mapping):
            raise ValueError("knowledge_index_revision_binding_invalid")
        required = {
            "source_scope",
            "source_id",
            "source_revision_id",
            "source_revision_digest",
        }
        if set(revision_binding) != required:
            raise ValueError("knowledge_index_revision_binding_invalid")
        normalized = {
            field: str(revision_binding.get(field) or "").strip()
            for field in sorted(required)
        }
        if (
            normalized["source_scope"] not in {"repo_path", "artifact", "wiki"}
            or normalized["source_id"]
            != f"bound-source:{normalized['source_revision_id']}"
            or re.fullmatch(
                r"srev_[0-9a-f]{64}", normalized["source_revision_id"]
            )
            is None
            or re.fullmatch(
                r"[0-9a-f]{64}", normalized["source_revision_digest"]
            )
            is None
            or not str(knowledge_index.get("id") or "").strip()
        ):
            raise ValueError("knowledge_index_revision_binding_invalid")
        metadata = knowledge_index.get("index_metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("knowledge_index_revision_binding_missing")
        for field, expected in normalized.items():
            if str(metadata.get(field) or "").strip() != expected:
                raise ValueError(
                    f"knowledge_index_revision_binding_{field}_mismatch"
                )
        for field in ("source_scope", "source_id"):
            if str(manifest.get(field) or "").strip() != normalized[field]:
                raise ValueError(
                    f"knowledge_index_manifest_{field}_mismatch"
                )
        return normalized

    @staticmethod
    def _load_source_manifest(output_dir: Path) -> dict[str, Any]:
        path = output_dir / "manifest.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("knowledge_index_manifest_invalid")
        size_bytes = path.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_SOURCE_MANIFEST_BYTES:
            raise ValueError("knowledge_index_manifest_invalid")
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("knowledge_index_manifest_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("knowledge_index_manifest_invalid")
        return payload

    def _assert_admissible_graph_artifact(self, path: Path) -> None:
        try:
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise RuntimeError("knowledge_index_graph_artifact_missing") from exc
        if size_bytes < 0 or size_bytes > self._max_graph_artifact_bytes:
            raise RuntimeError("knowledge_index_graph_artifact_too_large")

    @staticmethod
    def _resolve_output_dir(
        *,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> Path:
        value = str(run.get("output_dir") or knowledge_index.get("output_dir") or "").strip()
        if not value:
            raise RuntimeError("knowledge_index_output_directory_missing")
        path = Path(value)
        if path.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("knowledge_index_output_directory_missing") from exc
        if not resolved.is_dir() or resolved.is_symlink():
            raise RuntimeError("knowledge_index_output_directory_invalid")
        return resolved


__all__ = [
    "GRAPH_INDEX_FILENAME",
    "GRAPH_INDEX_SCHEMA",
    "GRAPH_VISUAL_METRICS_FILENAME",
    "GRAPH_VISUAL_OPTIONS_SCHEMA",
    "MAX_CONFIGURED_BLAST_RADIUS_SEEDS",
    "WorkerCodeCompassGraphArtifactMaterializer",
    "normalize_graph_visual_options",
]
