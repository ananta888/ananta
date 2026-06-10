"""Output writing helpers for process_project (SPLIT-033 follow-up).

Extracted from project_processor.py to keep the orchestrator focused on
pipeline control. This module owns the responsibility documented in
the SPLIT-033 plan:

  - Schreiben aller Output-Dateien (JSONL, partitioniert, Zips)
  - Manifest-Datei-Schreiben
  - Cache-Persistierung am Ende

It is intentionally side-effecting: it writes to ``out_dir`` and may
trigger the optional output bundle. The orchestrator passes in a
prepared ``manifest`` dict and the already-computed record lists, and
this module mutates the ``manifest`` dict in place with the actual
output paths/partition metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag_helper.application.output_partitions import write_partitioned_jsonl


def write_output_files(
    *,
    out_dir: Path,
    limits: Any,
    all_index: list[dict],
    all_details: list[dict],
    all_relations: list[dict],
    gem_partition_records: list[dict],
    xml_overview_records: list[dict],
    graph_nodes: list[dict],
    graph_edges: list[dict],
    benchmark_report: Any,
    duplicate_report: Any,
    error_entries: list[dict],
    error_log_file: Path | None,
    manifest: dict,
    cache_file: Path,
    next_cache: dict,
    cache_enabled: bool,
    build_embedding_records,
    build_context_records,
    write_output_bundle,
    save_incremental_cache,
    compact_manifest_fn,
    write_jsonl_fn,
    manifest_extras: dict | None = None,
) -> list[str]:
    """Write all pipeline outputs and return the list of written file names.

    Mutates ``manifest`` in place: populates ``output_bundle``,
    ``partitioned_outputs`` and the final file count for the bundle
    mode. The caller is expected to write ``manifest.json`` itself if
    it needs additional in-place mutations before serialising.
    """
    ultra_mode = limits.output_compaction_mode in {"ultra", "ultra-rich"}
    written_output_files: list[str] = []
    xsd_index_records = [
        record for record in all_index
        if str(record.get("kind") or "").startswith("xsd_")
    ]
    xsd_detail_records = [
        record for record in all_details
        if str(record.get("kind") or "").startswith("xsd_")
    ]
    xsd_relation_records = [
        record for record in all_relations
        if str(record.get("source_kind") or "").startswith("xsd_")
        or str(record.get("target_resolved") or "").startswith("xsd_")
        or str(record.get("file") or "").lower().endswith(".xsd")
    ]
    if not ultra_mode:
        write_jsonl_fn(out_dir / "index.jsonl", all_index)
        write_jsonl_fn(out_dir / "details.jsonl", all_details)
        written_output_files.extend(["index.jsonl", "details.jsonl"])
        if limits.relation_output_mode in {"combined", "both"}:
            write_jsonl_fn(out_dir / "relations.jsonl", all_relations)
            written_output_files.append("relations.jsonl")
        if limits.relation_output_mode in {"split", "both"}:
            relation_partition_paths = write_partitioned_jsonl(
                out_dir,
                "relations_by_type",
                all_relations,
                key_getter=lambda item: item.get("relation") or item.get("type"),
            )
            manifest["partitioned_outputs"]["relations"] = relation_partition_paths
            written_output_files.extend(relation_partition_paths)
        if limits.output_partition_mode == "by-kind":
            index_partition_paths = write_partitioned_jsonl(
                out_dir,
                "index_by_kind",
                all_index,
                key_getter=lambda item: item.get("kind"),
            )
            detail_partition_paths = write_partitioned_jsonl(
                out_dir,
                "details_by_kind",
                all_details,
                key_getter=lambda item: item.get("kind"),
            )
            manifest["partitioned_outputs"]["index"] = index_partition_paths
            manifest["partitioned_outputs"]["details"] = detail_partition_paths
            written_output_files.extend(index_partition_paths)
            written_output_files.extend(detail_partition_paths)
    elif xsd_index_records or xsd_detail_records or xsd_relation_records:
        xsd_partition_paths: list[str] = []
        if xsd_index_records:
            write_jsonl_fn(out_dir / "xsd_full" / "index.jsonl", xsd_index_records)
            xsd_partition_paths.append("xsd_full/index.jsonl")
        if xsd_detail_records:
            write_jsonl_fn(out_dir / "xsd_full" / "details.jsonl", xsd_detail_records)
            xsd_partition_paths.append("xsd_full/details.jsonl")
        if xsd_relation_records:
            write_jsonl_fn(out_dir / "xsd_full" / "relations.jsonl", xsd_relation_records)
            xsd_partition_paths.append("xsd_full/relations.jsonl")
        manifest["partitioned_outputs"]["xsd_full"] = xsd_partition_paths
        written_output_files.extend(xsd_partition_paths)
    if limits.gem_partition_mode in {"domain", "domain-rich"}:
        gem_partition_paths = write_partitioned_jsonl(
            out_dir,
            "gems_by_domain",
            gem_partition_records,
            key_getter=lambda item: item.get("domain"),
        )
        manifest["partitioned_outputs"]["gems"] = gem_partition_paths
        written_output_files.extend(gem_partition_paths)
    if xml_overview_records:
        write_jsonl_fn(out_dir / "xml_overview.jsonl", xml_overview_records)
        manifest["partitioned_outputs"]["xml_overview"] = ["xml_overview.jsonl"]
        written_output_files.append("xml_overview.jsonl")
    if not ultra_mode and limits.retrieval_output_mode in {"split", "both"}:
        write_jsonl_fn(out_dir / "embedding.jsonl", build_embedding_records(all_index))
        write_jsonl_fn(
            out_dir / "context.jsonl",
            build_context_records(all_details, limits.context_output_mode),
        )
        written_output_files.extend(["embedding.jsonl", "context.jsonl"])
    if limits.graph_export_mode in {"jsonl", "neo4j"}:
        write_jsonl_fn(out_dir / "graph_nodes.jsonl", graph_nodes)
        write_jsonl_fn(out_dir / "graph_edges.jsonl", graph_edges)
        written_output_files.extend(["graph_nodes.jsonl", "graph_edges.jsonl"])
    if benchmark_report is not None:
        with (out_dir / "benchmark.json").open("w", encoding="utf-8") as f:
            json.dump(benchmark_report, f, ensure_ascii=False, indent=2)
        written_output_files.append("benchmark.json")
    if duplicate_report is not None:
        with (out_dir / "duplicates.json").open("w", encoding="utf-8") as f:
            json.dump(duplicate_report, f, ensure_ascii=False, indent=2)
        written_output_files.append("duplicates.json")
    if error_log_file is not None:
        write_jsonl_fn(error_log_file, error_entries)
    if cache_enabled:
        save_incremental_cache(cache_file, next_cache)
    written_output_files.append("manifest.json")
    if limits.output_bundle_mode == "zip":
        manifest["output_bundle"]["path"] = str(out_dir / "output_bundle.zip")
        manifest["output_bundle"]["file_count"] = len(written_output_files)
    if manifest_extras:
        for key, value in manifest_extras.items():
            manifest[key] = value
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(
            compact_manifest_fn(manifest),
            f,
            ensure_ascii=False,
            indent=2,
        )
    if limits.output_bundle_mode == "zip":
        write_output_bundle(out_dir, written_output_files)
    return written_output_files
