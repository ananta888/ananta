from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rag_helper.application.benchmarking import build_benchmark_report
from rag_helper.application.duplicate_detection import build_duplicate_report
from rag_helper.application.generated_code import detect_generated_code
from rag_helper.application.importance_scoring import score_index_records
from rag_helper.application.incremental_cache import load_incremental_cache, save_incremental_cache
from rag_helper.application.manifest_stats import (
    collect_error_entries,
    collect_extension_stats,
    collect_skip_reason_counts,
    count_records_by_kind,
)
from rag_helper.application.output_formats import (
    build_context_records,
    build_embedding_records,
    build_graph_edges,
    build_graph_nodes,
)
from rag_helper.application.output_bundle import write_output_bundle
from rag_helper.application.output_partitions import write_partitioned_jsonl
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.relation_compaction import compact_relation_records, compact_relation_records_by_file
from rag_helper.application.specialized_chunkers import build_specialized_chunks
from rag_helper.application.summary_records import build_summary_records
from rag_helper.extractors.base import FileSkipped, JavaLikeExtractor
from rag_helper.filesystem.file_filters import exclude_gitignored_files, should_include_file
from rag_helper.filesystem.text_reader import read_text_file
from rag_helper.utils.ids import sha1_text


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, items) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def build_extractor(extractor_cls, **kwargs):
    try:
        return extractor_cls(**kwargs)
    except TypeError:
        return extractor_cls()


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    rel_path: str
    ext: str
    text: str | None
    size: int
    sha1: str | None


@dataclass(frozen=True)
class FileProcessingResult:
    rel_path: str
    index: list[dict]
    details: list[dict]
    relations: list[dict]
    manifest_entry: dict
    cache_entry: dict


def collect_files(
    root: Path,
    extensions: set[str],
    excludes: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if should_include_file(
            path=p,
            root=root,
            extensions=extensions,
            excluded_parts=excludes,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        ):
            files.append(p)
    return sorted(exclude_gitignored_files(root, files))


def build_package_type_index(
    snapshots: list[FileSnapshot],
    java_extractor: JavaLikeExtractor,
    reusable_cache_entries: dict[str, dict],
    cache_entries_out: dict[str, dict],
) -> tuple[dict[str, set[str]], list[dict]]:
    known_package_types: dict[str, set[str]] = defaultdict(set)
    scan_errors: list[dict] = []
    for snapshot in snapshots:
        if snapshot.ext != "java":
            continue
        if snapshot.text is None:
            continue
        cached_entry = reusable_cache_entries.get(snapshot.rel_path)
        cached_pre_scan = cached_entry.get("pre_scan") if cached_entry else None
        if cached_pre_scan:
            scan = cached_pre_scan
        else:
            try:
                scan = java_extractor.pre_scan_types(snapshot.rel_path, snapshot.text)
            except Exception as exc:
                scan_errors.append({
                    "file": snapshot.rel_path,
                    "ext": "java",
                    "stage": "pre_scan",
                    "error": str(exc),
                })
                continue
        try:
            if scan["package"]:
                for tn in scan["type_names"]:
                    known_package_types[scan["package"]].add(tn)
            cache_entry = cache_entries_out.setdefault(snapshot.rel_path, {})
            cache_entry["pre_scan"] = scan
        except Exception as exc:
            scan_errors.append({
                "file": snapshot.rel_path,
                "ext": "java",
                "stage": "pre_scan",
                "error": str(exc),
            })
    return dict(known_package_types), scan_errors


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


def build_file_snapshots(files: list[Path], root: Path) -> list[FileSnapshot]:
    snapshots: list[FileSnapshot] = []
    for path in files:
        rel_path = str(path.relative_to(root))
        ext = path.suffix.lower().lstrip(".")
        size = path.stat().st_size
        text = read_text_file(path)
        snapshots.append(FileSnapshot(
            path=path,
            rel_path=rel_path,
            ext=ext,
            text=text,
            size=size,
            sha1=sha1_text(text) if text is not None else None,
        ))
    return snapshots


def is_cache_entry_reusable(entry: dict | None, sha1: str | None, options_signature: str) -> bool:
    return bool(entry) and entry.get("sha1") == sha1 and entry.get("options_signature") == options_signature


def annotate_generated_records(records: list[dict], generated_info: dict) -> None:
    if not generated_info["is_generated"]:
        return
    for record in records:
        record["generated_code"] = True
        record["generated_code_reasons"] = generated_info["reasons"]


def emit_progress(
    processed_count: int,
    total_count: int,
    manifest_entry: dict,
    cache_hit_count: int,
    skip_count: int,
    error_count: int,
) -> None:
    percent = 100 if total_count == 0 else int((processed_count / total_count) * 100)
    state = "ok"
    if manifest_entry.get("cache_hit"):
        state = "cache_hit"
    elif manifest_entry.get("skipped"):
        state = "skipped"
    elif manifest_entry.get("error"):
        state = "error"
    print(
        f"[{processed_count}/{total_count} {percent:3d}%] "
        f"{manifest_entry.get('file', '<unknown>')} "
        f"state={state} skips={skip_count} errors={error_count} cache_hits={cache_hit_count}"
    )


def persist_cache_checkpoint(
    cache_file: Path,
    cache: dict,
    enabled: bool,
    changed_extensions: set[str] | None = None,
) -> None:
    if enabled:
        save_incremental_cache(cache_file, cache, changed_extensions=changed_extensions)


def build_extractors(
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    limits: ProcessingLimits,
    java_extractor_cls,
    adoc_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
    text_extractor_cls=None,
) -> dict[str, object]:
    extractors = {
        "java": java_extractor_cls(
            include_code_snippets=include_code_snippets,
            exclude_trivial_methods=exclude_trivial_methods,
            max_methods_per_class=limits.max_methods_per_class,
            relation_mode=limits.java_relation_mode,
            resolve_wildcard_imports=limits.resolve_wildcard_imports,
            mark_import_conflicts=limits.mark_import_conflicts,
            resolve_method_targets=limits.resolve_method_targets,
            resolve_framework_relations=limits.resolve_framework_relations,
            embedding_text_mode=limits.embedding_text_mode,
        ),
        "adoc": build_extractor(adoc_extractor_cls, embedding_text_mode=limits.embedding_text_mode),
        "xml": build_extractor(
            xml_extractor_cls,
            include_xml_node_details=include_xml_node_details,
            max_xml_nodes=limits.max_xml_nodes,
            xml_mode=limits.xml_mode,
            relation_mode=limits.xml_relation_mode,
            repetitive_child_threshold=limits.xml_repetitive_child_threshold,
            embedding_text_mode=limits.embedding_text_mode,
        ),
        "xsd": build_extractor(
            xsd_extractor_cls,
            max_xml_nodes=limits.max_xml_nodes,
            embedding_text_mode=limits.embedding_text_mode,
        ),
    }
    if text_extractor_cls is not None:
        text_extractor = build_extractor(text_extractor_cls, embedding_text_mode=limits.embedding_text_mode)
        extractors.update({
            "properties": text_extractor,
            "yaml": text_extractor,
            "yml": text_extractor,
            "sql": text_extractor,
            "md": text_extractor,
            "py": text_extractor,
            "ts": text_extractor,
            "tsx": text_extractor,
        })
    return extractors


def build_cache_entry(
    snapshot: FileSnapshot,
    options_signature: str,
    manifest_entry: dict,
    index: list[dict],
    details: list[dict],
    relations: list[dict],
    pre_scan: dict | None = None,
) -> dict:
    cache_entry = {
        "sha1": snapshot.sha1,
        "options_signature": options_signature,
        "manifest": manifest_entry,
        "index": index,
        "details": details,
        "relations": relations,
    }
    if pre_scan is not None:
        cache_entry["pre_scan"] = pre_scan
    return cache_entry


def process_snapshot(
    snapshot: FileSnapshot,
    options_signature: str,
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    limits: ProcessingLimits,
    java_extractor_cls,
    adoc_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
    known_package_types: dict[str, set[str]],
    text_extractor_cls=None,
    pre_scan: dict | None = None,
) -> FileProcessingResult:
    started_at = perf_counter()
    rel_path = snapshot.rel_path
    ext = snapshot.ext
    file_size_bytes = snapshot.size

    if limits.max_file_size_kb is not None and file_size_bytes > limits.max_file_size_kb * 1024:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "size": file_size_bytes,
            "skipped": True,
            "skip_reason": "max_file_size_kb_exceeded",
            "limit": limits.max_file_size_kb,
            "cache_hit": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "output_record_count": 0,
        }
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
        )

    text = snapshot.text
    if text is None:
        manifest_entry = {"file": rel_path, "ext": ext, "error": "unreadable", "cache_hit": False}
        manifest_entry["duration_ms"] = round((perf_counter() - started_at) * 1000, 3)
        manifest_entry["output_record_count"] = 0
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
        )

    generated_info = detect_generated_code(
        rel_path=rel_path,
        text=text,
        extra_comment_markers=list(limits.generated_comment_markers),
    )
    if limits.generated_code_mode == "exclude" and generated_info["is_generated"]:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "sha1": snapshot.sha1,
            "size": file_size_bytes,
            "skipped": True,
            "skip_reason": "generated_code_excluded",
            "generated_code": True,
            "generated_code_reasons": generated_info["reasons"],
            "cache_hit": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "output_record_count": 0,
        }
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
        )

    try:
        extractor = build_extractors(
            include_code_snippets=include_code_snippets,
            exclude_trivial_methods=exclude_trivial_methods,
            include_xml_node_details=include_xml_node_details,
            limits=limits,
            java_extractor_cls=java_extractor_cls,
            adoc_extractor_cls=adoc_extractor_cls,
            xml_extractor_cls=xml_extractor_cls,
            xsd_extractor_cls=xsd_extractor_cls,
            text_extractor_cls=text_extractor_cls,
        ).get(ext)
        if extractor is None:
            manifest_entry = {
                "file": rel_path,
                "ext": ext,
                "skipped": True,
                "skip_reason": "unsupported_extension",
                "cache_hit": False,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                "output_record_count": 0,
            }
            return FileProcessingResult(
                rel_path=rel_path,
                index=[],
                details=[],
                relations=[],
                manifest_entry=manifest_entry,
                cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
            )

        if ext == "java":
            idx, det, rel, stats = extractor.parse(
                rel_path=rel_path,
                text=text,
                known_package_types=known_package_types,
            )
        else:
            idx, det, rel, stats = extractor.parse(rel_path, text)

        rel, relation_compaction_stats = compact_relation_records(
            rel,
            max_relation_records_per_file=limits.max_relation_records_per_file,
        )
        if relation_compaction_stats:
            stats = dict(stats)
            stats["relation_compaction"] = relation_compaction_stats

        total_records = len(idx) + len(det) + len(rel)
        if limits.max_records_per_file is not None and total_records > limits.max_records_per_file:
            manifest_entry = {
                "file": rel_path,
                "ext": ext,
                "sha1": snapshot.sha1,
                "size": file_size_bytes,
                "skipped": True,
                "skip_reason": "max_records_per_file_exceeded",
                "generated_record_count": total_records,
                "limit": limits.max_records_per_file,
                "stats": stats,
                "cache_hit": False,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                "output_record_count": 0,
            }
            if generated_info["is_generated"]:
                manifest_entry["generated_code"] = True
                manifest_entry["generated_code_reasons"] = generated_info["reasons"]
            return FileProcessingResult(
                rel_path=rel_path,
                index=[],
                details=[],
                relations=[],
                manifest_entry=manifest_entry,
                cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
            )

        annotate_generated_records(idx, generated_info)
        annotate_generated_records(det, generated_info)
        score_index_records(idx, limits.importance_scoring_mode)

        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "sha1": snapshot.sha1,
            "size": file_size_bytes,
            "stats": stats,
            "cache_hit": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "output_record_count": len(idx) + len(det) + len(rel),
        }
        if relation_compaction_stats:
            manifest_entry["relation_compaction"] = relation_compaction_stats
        if generated_info["is_generated"]:
            manifest_entry["generated_code"] = True
            manifest_entry["generated_code_reasons"] = generated_info["reasons"]
        return FileProcessingResult(
            rel_path=rel_path,
            index=idx,
            details=det,
            relations=rel,
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, idx, det, rel, pre_scan),
        )
    except FileSkipped as skipped:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "sha1": snapshot.sha1,
            "size": file_size_bytes,
            "skipped": True,
            "skip_reason": skipped.reason,
            **skipped.details,
            "cache_hit": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "output_record_count": 0,
        }
        if generated_info["is_generated"]:
            manifest_entry["generated_code"] = True
            manifest_entry["generated_code_reasons"] = generated_info["reasons"]
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
        )
    except Exception as exc:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "error": str(exc),
            "cache_hit": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "output_record_count": 0,
        }
        if generated_info["is_generated"]:
            manifest_entry["generated_code"] = True
            manifest_entry["generated_code_reasons"] = generated_info["reasons"]
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(snapshot, options_signature, manifest_entry, [], [], [], pre_scan),
        )


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
) -> None:
    if not dry_run:
        ensure_dir(out_dir)
    cache_file = cache_file or (out_dir / ".code_to_rag_cache.json")
    java_extractor = build_extractors(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
        include_xml_node_details=include_xml_node_details,
        limits=limits,
        java_extractor_cls=java_extractor_cls,
        adoc_extractor_cls=adoc_extractor_cls,
        xml_extractor_cls=xml_extractor_cls,
        xsd_extractor_cls=xsd_extractor_cls,
        text_extractor_cls=text_extractor_cls,
    )["java"]

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
    known_package_types, pre_scan_errors = build_package_type_index(
        snapshots=snapshots,
        java_extractor=java_extractor,
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
    pending_snapshots: list[FileSnapshot] = []
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
                len(cached_entry.get("index", [])) + len(cached_entry.get("details", [])) + len(cached_entry.get("relations", [])),
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
    if max_workers == 1:
        for snapshot in pending_snapshots:
            result = process_snapshot(
                snapshot=snapshot,
                options_signature=options_signature,
                include_code_snippets=include_code_snippets,
                exclude_trivial_methods=exclude_trivial_methods,
                include_xml_node_details=include_xml_node_details,
                limits=limits,
                java_extractor_cls=java_extractor_cls,
                adoc_extractor_cls=adoc_extractor_cls,
                xml_extractor_cls=xml_extractor_cls,
                xsd_extractor_cls=xsd_extractor_cls,
                text_extractor_cls=text_extractor_cls,
                known_package_types=known_package_types,
                pre_scan=next_cache["files"].get(snapshot.rel_path, {}).get("pre_scan"),
            )
            processed_results[snapshot.rel_path] = result
            progress_processed += 1
            if result.manifest_entry.get("skipped"):
                progress_skips += 1
            if result.manifest_entry.get("error"):
                progress_errors += 1
            if show_progress:
                emit_progress(
                    processed_count=progress_processed,
                    total_count=len(snapshots),
                    manifest_entry=result.manifest_entry,
                    cache_hit_count=cache_hits,
                    skip_count=progress_skips,
                    error_count=progress_errors,
                )
            next_cache["files"][snapshot.rel_path] = result.cache_entry
            pending_checkpoint_extensions.add(snapshot.ext or "_noext")
            persist_cache_checkpoint(
                cache_file,
                next_cache,
                resume,
                changed_extensions=set(pending_checkpoint_extensions),
            )
            pending_checkpoint_extensions.clear()
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    process_snapshot,
                    snapshot=snapshot,
                    options_signature=options_signature,
                    include_code_snippets=include_code_snippets,
                    exclude_trivial_methods=exclude_trivial_methods,
                    include_xml_node_details=include_xml_node_details,
                    limits=limits,
                    java_extractor_cls=java_extractor_cls,
                    adoc_extractor_cls=adoc_extractor_cls,
                    xml_extractor_cls=xml_extractor_cls,
                    xsd_extractor_cls=xsd_extractor_cls,
                    text_extractor_cls=text_extractor_cls,
                    known_package_types=known_package_types,
                    pre_scan=next_cache["files"].get(snapshot.rel_path, {}).get("pre_scan"),
                ): snapshot.rel_path
                for snapshot in pending_snapshots
            }
            for future in as_completed(future_map):
                result = future.result()
                processed_results[result.rel_path] = result
                progress_processed += 1
                if result.manifest_entry.get("skipped"):
                    progress_skips += 1
                if result.manifest_entry.get("error"):
                    progress_errors += 1
                if show_progress:
                    emit_progress(
                        processed_count=progress_processed,
                        total_count=len(snapshots),
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

    for snapshot in pending_snapshots:
        result = processed_results[snapshot.rel_path]
        all_index.extend(result.index)
        all_details.extend(result.details)
        all_relations.extend(result.relations)
        manifest_files.append(result.manifest_entry)
        next_cache["files"].setdefault(snapshot.rel_path, result.cache_entry)

    error_entries = collect_error_entries(manifest_files)
    duplicate_report, duplicate_relations = build_duplicate_report(all_index, limits.duplicate_detection_mode)
    specialized_details, specialized_relations, specialized_stats = build_specialized_chunks(
        all_index,
        all_details,
        limits.specialized_chunker_mode,
        limits.embedding_text_mode,
    )
    summary_records, summary_stats = build_summary_records(all_index, limits.embedding_text_mode)
    all_index.extend(summary_records)
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
    benchmark_report = build_benchmark_report(manifest_files, limits.benchmark_mode)
    graph_nodes = build_graph_nodes(all_index, all_details, limits.graph_export_mode) \
        if limits.graph_export_mode in {"jsonl", "neo4j"} else []
    graph_edges = build_graph_edges(all_index, all_details, all_relations, limits.graph_export_mode) \
        if limits.graph_export_mode in {"jsonl", "neo4j"} else []

    manifest = {
        "project_root": str(root),
        "file_count": len(manifest_files),
        "index_record_count": len(all_index),
        "detail_record_count": len(all_details),
        "relation_record_count": len(all_relations),
        "embedding_record_count": len(build_embedding_records(all_index))
        if limits.retrieval_output_mode in {"split", "both"} else 0,
        "context_record_count": len(build_context_records(all_details))
        if limits.retrieval_output_mode in {"split", "both"} else 0,
        "graph_node_count": len(graph_nodes),
        "graph_edge_count": len(graph_edges),
        "benchmark": benchmark_report,
        "duplicate_detection": duplicate_report,
        "specialized_chunks": specialized_stats,
        "summary_records": summary_stats,
        "record_counts_by_kind": count_records_by_kind(all_index, all_details, all_relations),
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
            "path": str(out_dir / "output_bundle.zip") if limits.output_bundle_mode == "zip" else None,
        },
        "partitioned_outputs": {
            "mode": limits.output_partition_mode,
            "index": [],
            "details": [],
            "relations": [],
        },
        "files": manifest_files,
    }

    if not dry_run:
        written_output_files = ["index.jsonl", "details.jsonl"]
        write_jsonl(out_dir / "index.jsonl", all_index)
        write_jsonl(out_dir / "details.jsonl", all_details)
        if limits.relation_output_mode in {"combined", "both"}:
            write_jsonl(out_dir / "relations.jsonl", all_relations)
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
        if limits.retrieval_output_mode in {"split", "both"}:
            write_jsonl(out_dir / "embedding.jsonl", build_embedding_records(all_index))
            write_jsonl(out_dir / "context.jsonl", build_context_records(all_details))
            written_output_files.extend(["embedding.jsonl", "context.jsonl"])
        if limits.graph_export_mode in {"jsonl", "neo4j"}:
            write_jsonl(out_dir / "graph_nodes.jsonl", graph_nodes)
            write_jsonl(out_dir / "graph_edges.jsonl", graph_edges)
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
            write_jsonl(error_log_file, error_entries)
        if cache_enabled:
            save_incremental_cache(cache_file, next_cache)
        written_output_files.append("manifest.json")
        if limits.output_bundle_mode == "zip":
            manifest["output_bundle"]["path"] = str(out_dir / "output_bundle.zip")
            manifest["output_bundle"]["file_count"] = len(written_output_files)
        with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        if limits.output_bundle_mode == "zip":
            write_output_bundle(out_dir, written_output_files)

    print(f"{'Dry-run fertig' if dry_run else 'Fertig'}: {out_dir}")
    print(f"Dateien: {len(manifest_files)}")
    print(f"Index Records: {len(all_index)}")
    print(f"Detail Records: {len(all_details)}")
    print(f"Relation Records: {len(all_relations)}")
