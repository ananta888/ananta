"""Worker-owned materialization of revision-bound CodeCompass graph artifacts.

The Hub delegates an index job and receives only published artifacts.  This
module turns the completed worker-local CodeCompass output directory into the
two graph artifacts consumed by the Hub.  It deliberately has no task-queue or
Hub-persistence dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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


def _stable_graph_revision(records: list[dict[str, Any]]) -> str:
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
    digest = hashlib.sha256(
        _canonical_json(
            {
                "schema": "codecompass_graph_source_revision.v1",
                "records": revision_records,
            }
        )
    ).hexdigest()
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

    def materialize(
        self,
        *,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_dir = self._resolve_output_dir(knowledge_index=knowledge_index, run=run)
        normalized_options = normalize_graph_visual_options(options)
        loaded = CodeCompassOutputReader().load_from_output_dir(
            output_dir=output_dir,
            profile_name=str(run.get("profile_name") or knowledge_index.get("profile_name") or "default"),
            source_scope=str(knowledge_index.get("source_scope") or "artifact"),
            generated_at="not_recorded",
        )
        records = [
            _sanitize_record(item)
            for item in list(loaded.get("records") or [])
            if isinstance(item, Mapping)
        ]
        if _graph_export_required(knowledge_index, run) and not any(
            str((record.get("_provenance") or {}).get("output_kind") or "")
            .strip()
            .lower()
            in _GRAPH_NODE_OUTPUT_KINDS
            for record in records
        ):
            raise RuntimeError("knowledge_index_graph_output_empty")
        revision = _stable_graph_revision(records)
        for record in records:
            provenance = record.get("_provenance")
            if isinstance(provenance, dict):
                provenance["manifest_hash"] = revision
        records.sort(key=_canonical_json)

        store = CodeCompassGraphStore(index_path=output_dir / GRAPH_INDEX_FILENAME)
        store.rebuild_from_output_records(records=records, manifest_hash=revision)
        metrics = materialize_graph_visual_metrics(
            graph_store=store,
            include_advanced_metrics=normalized_options["include_advanced_metrics"],
            blast_radius_seeds=normalized_options["blast_radius_seeds"],
        )
        graph_payload = store.load()
        actual_revision = str((graph_payload.get("state") or {}).get("manifest_hash") or "")
        if actual_revision != revision or str(metrics.get("graph_revision") or "") != revision:
            raise RuntimeError("codecompass_graph_artifact_revision_mismatch")
        if not verify_visual_metrics_content_hash(metrics):
            raise RuntimeError("codecompass_graph_visual_metrics_hash_invalid")
        return {
            "schema": "codecompass_graph_artifact_materialization.v1",
            "graph_revision": revision,
            "graph_index_path": str(output_dir / GRAPH_INDEX_FILENAME),
            "visual_metrics_path": str(output_dir / GRAPH_VISUAL_METRICS_FILENAME),
            "visual_metrics_content_hash": str(metrics.get("content_hash") or ""),
            "options": normalized_options,
        }

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
