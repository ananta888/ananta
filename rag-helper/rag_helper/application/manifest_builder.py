"""Manifest and post-processing aggregation helpers (SPLIT-033 follow-up).

Extracted from project_processor.py to keep the orchestrator focused on
pipeline control. The orchestrator delegates to this module for the
``compute_aggregates`` + ``build_manifest_dict`` pair - everything that
turns raw per-file results into the final manifest payload.

Responsibility boundary:

  - run all post-processing stages (specialized chunks, summaries,
    duplicate relations, output compaction, gem partitions, XML
    overview, benchmark, graph)
  - mutate the in-memory ``all_*`` lists in place (the caller passed
    them by reference)
  - assemble the manifest dict that is later serialised by
    output_writer
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag_helper.application.benchmarking import build_benchmark_report
from rag_helper.application.duplicate_detection import build_duplicate_report
from rag_helper.application.gem_partitions import build_gem_partition_records
from rag_helper.application.manifest_stats import (
    collect_error_entries,
    collect_extension_stats,
    collect_skip_reason_counts,
    count_records_by_kind,
)
from rag_helper.application.output_compaction import compact_output_records
from rag_helper.application.output_formats import (
    build_context_records,
    build_embedding_records,
    build_graph_edges,
    build_graph_nodes,
)
from rag_helper.application.relation_compaction import compact_relation_records_by_file
from rag_helper.application.specialized_chunkers import build_specialized_chunks
from rag_helper.application.summary_records import build_component_catalog_markdown, build_summary_records
from rag_helper.application.xml_overview import build_xml_overview_records


def compute_post_processing(
    *,
    all_index: list[dict],
    all_details: list[dict],
    all_relations: list[dict],
    manifest_files: list[dict],
    limits: Any,
    llm_narrative_endpoint: str | None = None,
    llm_narrative_model: str | None = None,
) -> dict:
    """Run all post-processing stages and return the aggregated payloads.

    Mutates ``all_index``/``all_details``/``all_relations`` and
    ``manifest_files`` in place (summary records, specialized chunks,
    duplicate relations, relation compaction, output compaction) and
    returns the per-mode data needed for the manifest dict.
    """
    error_entries = collect_error_entries(manifest_files)
    duplicate_report, duplicate_relations = build_duplicate_report(
        all_index, limits.duplicate_detection_mode
    )
    specialized_details, specialized_relations, specialized_stats = (
        build_specialized_chunks(
            all_index,
            all_details,
            limits.specialized_chunker_mode,
            limits.embedding_text_mode,
        )
    )
    summary_records, summary_stats = build_summary_records(
        all_index,
        limits.embedding_text_mode,
        llm_narrative_endpoint=llm_narrative_endpoint,
        llm_narrative_model=llm_narrative_model,
    )
    all_index.extend(summary_records)
    component_catalog_markdown = build_component_catalog_markdown(summary_records)
    all_details.extend(specialized_details)
    all_relations.extend(specialized_relations)
    all_relations.extend(duplicate_relations)
    all_relations, post_relation_compaction = compact_relation_records_by_file(
        all_relations,
        max_relation_records_per_file=limits.max_relation_records_per_file,
    )
    if post_relation_compaction:
        for manifest_entry in manifest_files:
            file_key = manifest_entry.get("file")
            if file_key in post_relation_compaction:
                manifest_entry["relation_compaction"] = post_relation_compaction[file_key]
    all_index, all_details, all_relations = compact_output_records(
        all_index,
        all_details,
        all_relations,
        limits.output_compaction_mode,
    )
    gem_partition_records = build_gem_partition_records(
        all_index,
        all_details,
        all_relations,
        limits.gem_partition_mode,
    )
    xml_overview_records = build_xml_overview_records(
        all_index, limits.xml_overview_mode
    )
    benchmark_report = build_benchmark_report(manifest_files, limits.benchmark_mode)
    graph_nodes = (
        build_graph_nodes(all_index, all_details, limits.graph_export_mode)
        if limits.graph_export_mode in {"jsonl", "neo4j"}
        else []
    )
    graph_edges = (
        build_graph_edges(all_index, all_details, all_relations, limits.graph_export_mode)
        if limits.graph_export_mode in {"jsonl", "neo4j"}
        else []
    )
    return {
        "error_entries": error_entries,
        "duplicate_report": duplicate_report,
        "specialized_stats": specialized_stats,
        "summary_stats": summary_stats,
        "gem_partition_records": gem_partition_records,
        "xml_overview_records": xml_overview_records,
        "benchmark_report": benchmark_report,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "component_catalog_markdown": component_catalog_markdown,
    }


def build_manifest_dict(
    *,
    root: Path,
    manifest_files: list[dict],
    all_index: list[dict],
    all_details: list[dict],
    all_relations: list[dict],
    graph_nodes: list[dict],
    graph_edges: list[dict],
    benchmark_report: Any,
    duplicate_report: Any,
    specialized_stats: Any,
    summary_stats: Any,
    error_entries: list[dict],
    cache_file: Path,
    cache_enabled: bool,
    rebuild: bool,
    resume: bool,
    cache_hits: int,
    cache_misses: int,
    max_workers: int,
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    incremental: bool,
    dry_run: bool,
    show_progress: bool,
    error_log_file: Path | None,
    known_package_types: dict[str, set[str]],
    limits: Any,
    out_dir: Path,
) -> dict:
    """Assemble the final manifest dict from the post-processing outputs."""
    return {
        "project_root": str(root),
        "file_count": len(manifest_files),
        "index_record_count": len(all_index),
        "detail_record_count": len(all_details),
        "relation_record_count": len(all_relations),
        "embedding_record_count": len(build_embedding_records(all_index))
        if limits.retrieval_output_mode in {"split", "both"}
        else 0,
        "context_record_count": len(
            build_context_records(all_details, limits.context_output_mode)
        )
        if limits.retrieval_output_mode in {"split", "both"}
        else 0,
        "graph_node_count": len(graph_nodes),
        "graph_edge_count": len(graph_edges),
        "benchmark": benchmark_report,
        "duplicate_detection": duplicate_report,
        "specialized_chunks": specialized_stats,
        "summary_records": summary_stats,
        "record_counts_by_kind": count_records_by_kind(
            all_index, all_details, all_relations
        ),
        "cache_file": str(cache_file),
        "cache_enabled": cache_enabled,
        "cache_rebuilt": rebuild,
        "resume_enabled": resume,
        "cache_hit_count": cache_hits,
        "cache_miss_count": cache_misses,
        "effective_max_workers": max_workers,
        "skip_reason_counts": collect_skip_reason_counts(manifest_files),
        "errors": error_entries,
        "error_count": len(error_entries),
        "error_log_file": str(error_log_file) if error_log_file else None,
        "extension_stats": collect_extension_stats(manifest_files),
        "options": {
            "include_code_snippets": include_code_snippets,
            "exclude_trivial_methods": exclude_trivial_methods,
            "include_xml_node_details": include_xml_node_details,
            "include_globs": include_globs or [],
            "exclude_globs": exclude_globs or [],
            "incremental": incremental,
            "rebuild": rebuild,
            "resume": resume,
            "dry_run": dry_run,
            "show_progress": show_progress,
            "error_log_file": str(error_log_file) if error_log_file else None,
            "max_workers": max_workers,
            **limits.as_options(),
        },
        "package_type_index": {k: sorted(v) for k, v in known_package_types.items()},
        "output_bundle": {
            "mode": limits.output_bundle_mode,
            "path": str(out_dir / "output_bundle.zip")
            if limits.output_bundle_mode == "zip"
            else None,
        },
        "partitioned_outputs": {
            "mode": limits.output_partition_mode,
            "index": [],
            "details": [],
            "relations": [],
            "gems": [],
            "xsd_full": [],
            "xml_overview": [],
        },
        "files": manifest_files,
    }
