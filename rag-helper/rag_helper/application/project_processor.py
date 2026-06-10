"""Top-level project orchestration (SPLIT-033).

After SPLIT-033, this module contains only the pipeline orchestrator
(``process_project``) plus the small tightly-coupled helpers it owns.
File scan, package pre-scan and per-file extraction live in
``file_scanner.py`` and ``document_extractor.py``; post-processing
aggregation and manifest assembly in ``manifest_builder.py``; output
writing in ``output_writer.py``.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rag_helper.application.document_extractor import (
    FileProcessingResult,
    build_extractors,
    emit_progress,
    persist_cache_checkpoint,
    process_snapshot,
)
from rag_helper.application.file_scanner import (
    build_file_snapshots,
    build_package_type_index,
    collect_files,
    is_cache_entry_reusable,
)
from rag_helper.application.incremental_cache import (
    load_incremental_cache,
    save_incremental_cache,
)
from rag_helper.application.manifest_builder import build_manifest_dict, compute_post_processing
from rag_helper.application.output_bundle import write_output_bundle
from rag_helper.application.output_formats import (
    build_context_records,
    build_embedding_records,
)
from rag_helper.application.output_writer import write_output_files
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.utils.ids import sha1_text


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def build_options_signature(
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    limits: ProcessingLimits,
) -> str:
    payload = {
        "include_code_snippets": include_code_snippets,
        "exclude_trivial_methods": exclude_trivial_methods,
        "include_xml_node_details": include_xml_node_details,
        "include_globs": include_globs or [],
        "exclude_globs": exclude_globs or [],
        **limits.as_options(),
    }
    return sha1_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def process_project(
    root: Path,
    out_dir: Path,
    extensions: set[str],
    excludes: set[str],
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    limits: ProcessingLimits,
    java_extractor_cls,
    adoc_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
    text_extractor_cls=None,
    incremental: bool = False,
    rebuild: bool = False,
    resume: bool = False,
    cache_file: Path | None = None,
    dry_run: bool = False,
    show_progress: bool = False,
    error_log_file: Path | None = None,
    csharp_extractor_cls=None,
) -> None:
    if not dry_run:
        ensure_dir(out_dir)
    cache_file = cache_file or (out_dir / ".code_to_rag_cache.json")
    extractors = build_extractors(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
        include_xml_node_details=include_xml_node_details,
        limits=limits,
        java_extractor_cls=java_extractor_cls,
        csharp_extractor_cls=csharp_extractor_cls,
        adoc_extractor_cls=adoc_extractor_cls,
        xml_extractor_cls=xml_extractor_cls,
        xsd_extractor_cls=xsd_extractor_cls,
        text_extractor_cls=text_extractor_cls,
    )
    java_extractor = extractors["java"]
    csharp_extractor = extractors.get("cs")

    files = collect_files(
        root=root,
        extensions=extensions,
        excludes=excludes,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
    )
    snapshots = build_file_snapshots(files, root)
    options_signature = build_options_signature(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
        include_xml_node_details=include_xml_node_details,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        limits=limits,
    )
    cache_enabled = incremental or rebuild or resume
    loaded_cache = {"version": 1, "files": {}}
    if cache_enabled and not rebuild:
        loaded_cache = load_incremental_cache(cache_file)
    loaded_cache_files = loaded_cache.get("files", {})
    reusable_cache_entries = {
        snapshot.rel_path: loaded_cache_files[snapshot.rel_path]
        for snapshot in snapshots
        if is_cache_entry_reusable(
            loaded_cache_files.get(snapshot.rel_path),
            snapshot.sha1,
            options_signature,
        )
    }
    next_cache = {
        "version": 1,
        "options_signature": options_signature,
        "files": {},
    }
    known_package_types, known_namespace_types, pre_scan_errors = build_package_type_index(
        snapshots=snapshots,
        java_extractor=java_extractor,
        csharp_extractor=csharp_extractor,
        reusable_cache_entries=reusable_cache_entries,
        cache_entries_out=next_cache["files"],
    )

    all_index: list[dict] = []
    all_details: list[dict] = []
    all_relations: list[dict] = []
    manifest_files: list[dict] = list(pre_scan_errors)
    cache_hits = 0
    cache_misses = 0
    progress_processed = 0
    progress_skips = 0
    progress_errors = len(pre_scan_errors)
    processed_results: dict[str, FileProcessingResult] = {}
    pending_snapshots: list = []
    pending_checkpoint_extensions: set[str] = set()

    for snapshot in snapshots:
        rel_path = snapshot.rel_path
        cached_entry = reusable_cache_entries.get(rel_path)
        if cached_entry:
            all_index.extend(cached_entry.get("index", []))
            all_details.extend(cached_entry.get("details", []))
            all_relations.extend(cached_entry.get("relations", []))
            manifest_entry = dict(cached_entry.get("manifest", {}))
            manifest_entry["cache_hit"] = True
            manifest_entry.setdefault("duration_ms", 0.0)
            manifest_entry.setdefault(
                "output_record_count",
                len(cached_entry.get("index", []))
                + len(cached_entry.get("details", []))
                + len(cached_entry.get("relations", [])),
            )
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = dict(cached_entry)
            cache_hits += 1
            progress_processed += 1
            if manifest_entry.get("skipped"):
                progress_skips += 1
            if manifest_entry.get("error"):
                progress_errors += 1
            if show_progress:
                emit_progress(
                    processed_count=progress_processed,
                    total_count=len(snapshots),
                    manifest_entry=manifest_entry,
                    cache_hit_count=cache_hits,
                    skip_count=progress_skips,
                    error_count=progress_errors,
                )
            pending_checkpoint_extensions.add(snapshot.ext or "_noext")
            continue
        cache_misses += 1
        pending_snapshots.append(snapshot)

    max_workers = max(1, min(limits.max_workers, len(pending_snapshots) or 1, os.cpu_count() or 1))

    def _record_result(result, total: int) -> None:
        nonlocal progress_processed, progress_skips, progress_errors
        processed_results[result.rel_path] = result
        progress_processed += 1
        if result.manifest_entry.get("skipped"):
            progress_skips += 1
        if result.manifest_entry.get("error"):
            progress_errors += 1
        if show_progress:
            emit_progress(
                processed_count=progress_processed,
                total_count=total,
                manifest_entry=result.manifest_entry,
                cache_hit_count=cache_hits,
                skip_count=progress_skips,
                error_count=progress_errors,
            )
        next_cache["files"][result.rel_path] = result.cache_entry
        pending_checkpoint_extensions.add(result.manifest_entry.get("ext") or "_noext")
        persist_cache_checkpoint(
            cache_file,
            next_cache,
            resume,
            changed_extensions=set(pending_checkpoint_extensions),
        )
        pending_checkpoint_extensions.clear()

    if max_workers == 1:
        for snapshot in pending_snapshots:
            result = process_snapshot(
                snapshot,
                options_signature,
                include_code_snippets,
                exclude_trivial_methods,
                include_xml_node_details,
                limits,
                java_extractor_cls,
                adoc_extractor_cls,
                xml_extractor_cls,
                xsd_extractor_cls,
                known_package_types,
                known_namespace_types,
                text_extractor_cls=text_extractor_cls,
                pre_scan=next_cache["files"].get(snapshot.rel_path, {}).get("pre_scan"),
                csharp_extractor_cls=csharp_extractor_cls,
            )
            _record_result(result, total=len(snapshots))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    process_snapshot,
                    snapshot,
                    options_signature,
                    include_code_snippets,
                    exclude_trivial_methods,
                    include_xml_node_details,
                    limits,
                    java_extractor_cls,
                    adoc_extractor_cls,
                    xml_extractor_cls,
                    xsd_extractor_cls,
                    known_package_types,
                    known_namespace_types,
                    text_extractor_cls=text_extractor_cls,
                    pre_scan=next_cache["files"].get(snapshot.rel_path, {}).get("pre_scan"),
                    csharp_extractor_cls=csharp_extractor_cls,
                ): snapshot.rel_path
                for snapshot in pending_snapshots
            }
            for future in as_completed(future_map):
                _record_result(future.result(), total=len(snapshots))

    for snapshot in pending_snapshots:
        result = processed_results[snapshot.rel_path]
        all_index.extend(result.index)
        all_details.extend(result.details)
        all_relations.extend(result.relations)
        manifest_files.append(result.manifest_entry)
        next_cache["files"].setdefault(snapshot.rel_path, result.cache_entry)

    aggregates = compute_post_processing(
        all_index=all_index,
        all_details=all_details,
        all_relations=all_relations,
        manifest_files=manifest_files,
        limits=limits,
    )
    error_entries = aggregates["error_entries"]
    duplicate_report = aggregates["duplicate_report"]
    specialized_stats = aggregates["specialized_stats"]
    summary_stats = aggregates["summary_stats"]
    gem_partition_records = aggregates["gem_partition_records"]
    xml_overview_records = aggregates["xml_overview_records"]
    benchmark_report = aggregates["benchmark_report"]
    graph_nodes = aggregates["graph_nodes"]
    graph_edges = aggregates["graph_edges"]

    manifest = build_manifest_dict(
        root=root,
        manifest_files=manifest_files,
        all_index=all_index,
        all_details=all_details,
        all_relations=all_relations,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        benchmark_report=benchmark_report,
        duplicate_report=duplicate_report,
        specialized_stats=specialized_stats,
        summary_stats=summary_stats,
        error_entries=error_entries,
        cache_file=cache_file,
        cache_enabled=cache_enabled,
        rebuild=rebuild,
        resume=resume,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        max_workers=max_workers,
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
        include_xml_node_details=include_xml_node_details,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        incremental=incremental,
        dry_run=dry_run,
        show_progress=show_progress,
        error_log_file=error_log_file,
        known_package_types=known_package_types,
        limits=limits,
        out_dir=out_dir,
    )

    if not dry_run:
        write_output_files(
            out_dir=out_dir,
            limits=limits,
            all_index=all_index,
            all_details=all_details,
            all_relations=all_relations,
            gem_partition_records=gem_partition_records,
            xml_overview_records=xml_overview_records,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            benchmark_report=benchmark_report,
            duplicate_report=duplicate_report,
            error_entries=error_entries,
            error_log_file=error_log_file,
            manifest=manifest,
            cache_file=cache_file,
            next_cache=next_cache,
            cache_enabled=cache_enabled,
            build_embedding_records=build_embedding_records,
            build_context_records=build_context_records,
            write_output_bundle=write_output_bundle,
            save_incremental_cache=save_incremental_cache,
            compact_manifest_fn=lambda m: _compact_manifest(m, limits.manifest_output_mode),
            write_jsonl_fn=write_jsonl,
        )

    print(f"{'Dry-run fertig' if dry_run else 'Fertig'}: {out_dir}")
    print(f"Dateien: {len(manifest_files)}")
    print(f"Index Records: {len(all_index)}")
    print(f"Detail Records: {len(all_details)}")
    print(f"Relation Records: {len(all_relations)}")


def _compact_manifest(manifest: dict, mode: str) -> dict:
    if mode != "compact":
        return manifest

    compacted = dict(manifest)
    compacted["files_omitted_count"] = len(manifest.get("files", []))
    compacted["files"] = []
    compacted["package_type_index_summary"] = {
        "package_count": len(manifest.get("package_type_index", {})),
        "type_count": sum(
            len(types) for types in manifest.get("package_type_index", {}).values()
        ),
    }
    compacted["package_type_index"] = {}
    return compacted
