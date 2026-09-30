"""Source-record normalization and manifest projection for rag-helper indices.

Split out of ``rag_helper_index_service.RagHelperIndexService.index_source_records``
(SRP): the service keeps run orchestration and persistence; this module owns
the pure parts -- record normalization/chunking, the source file set and the
manifest documents written next to an index run.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.services.rag_index_chunker import chunk_wiki_records

_LOGGER = logging.getLogger("agent.services.rag_helper_index_service")


def normalize_source_records(
    *, normalized_scope: str, source_id: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate and copy source records; wiki records are chunked into sentences."""
    if not records:
        raise ValueError("source_records_required")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("invalid_source_records")
    normalized_records = [dict(record) for record in records]
    if normalized_scope == "wiki":
        normalized_records = chunk_wiki_records(
            source_id=source_id,
            records=normalized_records,
        )
    if not normalized_records:
        raise ValueError("source_records_empty_after_normalization")
    return normalized_records


def source_record_files(records: list[dict[str, Any]]) -> set[str]:
    source_files = {
        str(
            (
                (item.get("metadata") or {}).get("relative_path")
                if isinstance(item.get("metadata"), dict)
                else ""
            )
            or item.get("file")
            or item.get("path")
            or ""
        ).strip()
        for item in records
    }
    source_files.discard("")
    return source_files


def wiki_codecompass_manifest(
    manifest: dict[str, Any],
    *,
    normalized_scope: str,
    records: list[dict[str, Any]],
    normalized_records: list[dict[str, Any]],
    streaming: bool,
) -> dict[str, Any]:
    """Add the chunking summary to a CodeCompass-prerendered wiki manifest."""
    return {
        **manifest,
        "chunking": {
            "source_scope": normalized_scope,
            "input_record_count": len(records) if not streaming else 0,
            "normalized_record_count": (
                len(normalized_records) if not streaming else manifest.get("index_record_count", 0)
            ),
            "strategy": (
                "wiki_streaming_codecompass_prerender"
                if streaming
                else "wiki_sentence_chunks+wiki_streaming_codecompass_prerender"
            ),
        },
    }


def source_record_manifest(
    *,
    normalized_scope: str,
    source_id: str,
    profile: dict[str, Any],
    records: list[dict[str, Any]],
    normalized_records: list[dict[str, Any]],
    serialized_count: int,
    source_files: set[str],
    graph_manifest: dict[str, Any],
    graph_export_mode: str,
) -> dict[str, Any]:
    """Manifest of an identity (optionally CodeCompass-graph enriched) source-record index."""
    return {
        "source_scope": normalized_scope,
        "source_id": source_id,
        "profile_name": profile["name"],
        "file_count": int(graph_manifest.get("file_count", len(source_files))),
        "index_record_count": serialized_count,
        "detail_record_count": int(graph_manifest.get("semantic_node_count", 0)),
        "relation_record_count": int(graph_manifest.get("graph_edge_count", 0))
        + int(graph_manifest.get("semantic_edge_count", 0)),
        "error_count": int(graph_manifest.get("diagnostic_count", 0)),
        "partitioned_outputs": dict(graph_manifest.get("partitioned_outputs") or {}),
        **(
            {"semantic_budget": dict(graph_manifest["semantic_budget"])}
            if graph_manifest.get("semantic_budget")
            else {}
        ),
        "graph_export_mode": graph_export_mode,
        "deterministic_order": "json_sort_keys",
        "chunking": {
            "source_scope": normalized_scope,
            "input_record_count": len(records),
            "normalized_record_count": len(normalized_records),
            "strategy": (
                "wiki_sentence_chunks"
                if normalized_scope == "wiki"
                else ("identity+codecompass_graph" if graph_manifest else "identity")
            ),
        },
        "generated_at": time.time(),
    }


def manifest_summary_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Index metadata projected from a completed run manifest."""
    return {
        "manifest_summary": {
            "file_count": manifest.get("file_count", 0),
            "index_record_count": manifest.get("index_record_count", 0),
            "detail_record_count": manifest.get("detail_record_count", 0),
            "relation_record_count": manifest.get("relation_record_count", 0),
            "error_count": manifest.get("error_count", 0),
        },
        "available_outputs": manifest.get("partitioned_outputs", {}),
    }


def record_file_type_metrics_snapshot(source_metadata: dict[str, Any] | None) -> None:
    metric_snapshot = (source_metadata or {}).get("file_type_metrics_snapshot")
    if not isinstance(metric_snapshot, list):
        return
    try:
        from agent.services.file_type_metrics_service import get_file_type_metrics_service

        get_file_type_metrics_service().observe_snapshot(
            pipeline="setup_index",
            snapshot=metric_snapshot,
        )
    except Exception as exc:
        # Metrics must never change the persisted index outcome.
        _LOGGER.warning(
            "CodeCompass file-type metrics snapshot could not be recorded: %s",
            exc,
        )


__all__ = [
    "manifest_summary_metadata",
    "normalize_source_records",
    "record_file_type_metrics_snapshot",
    "source_record_files",
    "source_record_manifest",
    "wiki_codecompass_manifest",
]
