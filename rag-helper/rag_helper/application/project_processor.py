from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rag_helper.application.generated_code import detect_generated_code
from rag_helper.application.importance_scoring import score_index_records
from rag_helper.application.incremental_cache import load_incremental_cache, save_incremental_cache
from rag_helper.application.manifest_stats import (
    collect_error_entries,
    collect_extension_stats,
    collect_skip_reason_counts,
    count_records_by_kind,
)
from rag_helper.application.output_formats import build_context_records, build_embedding_records
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.extractors.base import FileSkipped, JavaLikeExtractor
from rag_helper.filesystem.file_filters import should_include_file
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


def collect_files(
    root: Path,
    extensions: set[str],
    excludes: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
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
    return sorted(files)


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
    incremental: bool = False,
    rebuild: bool = False,
    cache_file: Path | None = None,
) -> None:
    ensure_dir(out_dir)
    cache_file = cache_file or (out_dir / ".code_to_rag_cache.json")

    java_extractor = java_extractor_cls(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
        max_methods_per_class=limits.max_methods_per_class,
        resolve_wildcard_imports=limits.resolve_wildcard_imports,
        mark_import_conflicts=limits.mark_import_conflicts,
        resolve_method_targets=limits.resolve_method_targets,
        resolve_framework_relations=limits.resolve_framework_relations,
        embedding_text_mode=limits.embedding_text_mode,
    )
    extractors = {
        "java": java_extractor,
        "adoc": build_extractor(adoc_extractor_cls, embedding_text_mode=limits.embedding_text_mode),
        "xml": build_extractor(
            xml_extractor_cls,
            include_xml_node_details=include_xml_node_details,
            max_xml_nodes=limits.max_xml_nodes,
            xml_mode=limits.xml_mode,
            repetitive_child_threshold=limits.xml_repetitive_child_threshold,
            embedding_text_mode=limits.embedding_text_mode,
        ),
        "xsd": build_extractor(
            xsd_extractor_cls,
            max_xml_nodes=limits.max_xml_nodes,
            embedding_text_mode=limits.embedding_text_mode,
        ),
    }

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
    loaded_cache = {"version": 1, "files": {}}
    if incremental and not rebuild:
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

    for snapshot in snapshots:
        rel_path = snapshot.rel_path
        ext = snapshot.ext
        file_size_bytes = snapshot.size
        cached_entry = reusable_cache_entries.get(rel_path)
        if cached_entry:
            all_index.extend(cached_entry.get("index", []))
            all_details.extend(cached_entry.get("details", []))
            all_relations.extend(cached_entry.get("relations", []))
            manifest_entry = dict(cached_entry.get("manifest", {}))
            manifest_entry["cache_hit"] = True
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = dict(cached_entry)
            cache_hits += 1
            continue

        cache_misses += 1
        if limits.max_file_size_kb is not None and file_size_bytes > limits.max_file_size_kb * 1024:
            manifest_entry = {
                "file": rel_path,
                "ext": ext,
                "size": file_size_bytes,
                "skipped": True,
                "skip_reason": "max_file_size_kb_exceeded",
                "limit": limits.max_file_size_kb,
                "cache_hit": False,
            }
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": [],
                "details": [],
                "relations": [],
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }
            continue
        text = snapshot.text
        if text is None:
            manifest_entry = {"file": rel_path, "ext": ext, "error": "unreadable", "cache_hit": False}
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": [],
                "details": [],
                "relations": [],
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }
            continue

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
            }
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": [],
                "details": [],
                "relations": [],
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }
            continue

        try:
            extractor = extractors.get(ext)
            if extractor is None:
                continue

            if ext == "java":
                idx, det, rel, stats = extractor.parse(
                    rel_path=rel_path,
                    text=text,
                    known_package_types=known_package_types,
                )
            else:
                idx, det, rel, stats = extractor.parse(rel_path, text)

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
                }
                if generated_info["is_generated"]:
                    manifest_entry["generated_code"] = True
                    manifest_entry["generated_code_reasons"] = generated_info["reasons"]
                manifest_files.append(manifest_entry)
                next_cache["files"][rel_path] = {
                    "sha1": snapshot.sha1,
                    "options_signature": options_signature,
                    "manifest": manifest_entry,
                    "index": [],
                    "details": [],
                    "relations": [],
                    **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
                }
                continue

            all_index.extend(idx)
            all_details.extend(det)
            all_relations.extend(rel)
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
            }
            if generated_info["is_generated"]:
                manifest_entry["generated_code"] = True
                manifest_entry["generated_code_reasons"] = generated_info["reasons"]
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": idx,
                "details": det,
                "relations": rel,
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }
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
            }
            if generated_info["is_generated"]:
                manifest_entry["generated_code"] = True
                manifest_entry["generated_code_reasons"] = generated_info["reasons"]
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": [],
                "details": [],
                "relations": [],
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }
        except Exception as e:
            manifest_entry = {
                "file": rel_path,
                "ext": ext,
                "error": str(e),
                "cache_hit": False,
            }
            if generated_info["is_generated"]:
                manifest_entry["generated_code"] = True
                manifest_entry["generated_code_reasons"] = generated_info["reasons"]
            manifest_files.append(manifest_entry)
            next_cache["files"][rel_path] = {
                "sha1": snapshot.sha1,
                "options_signature": options_signature,
                "manifest": manifest_entry,
                "index": [],
                "details": [],
                "relations": [],
                **({"pre_scan": next_cache["files"].get(rel_path, {}).get("pre_scan")} if ext == "java" else {}),
            }

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
        "record_counts_by_kind": count_records_by_kind(all_index, all_details, all_relations),
        "cache_file": str(cache_file),
        "cache_enabled": incremental,
        "cache_rebuilt": rebuild,
        "cache_hit_count": cache_hits,
        "cache_miss_count": cache_misses,
        "skip_reason_counts": collect_skip_reason_counts(manifest_files),
        "errors": collect_error_entries(manifest_files),
        "error_count": len(collect_error_entries(manifest_files)),
        "extension_stats": collect_extension_stats(manifest_files),
        "options": {
            "include_code_snippets": include_code_snippets,
            "exclude_trivial_methods": exclude_trivial_methods,
            "include_xml_node_details": include_xml_node_details,
            "include_globs": include_globs or [],
            "exclude_globs": exclude_globs or [],
            "incremental": incremental,
            "rebuild": rebuild,
            **limits.as_options(),
        },
        "package_type_index": {k: sorted(v) for k, v in known_package_types.items()},
        "files": manifest_files,
    }

    write_jsonl(out_dir / "index.jsonl", all_index)
    write_jsonl(out_dir / "details.jsonl", all_details)
    write_jsonl(out_dir / "relations.jsonl", all_relations)
    if limits.retrieval_output_mode in {"split", "both"}:
        write_jsonl(out_dir / "embedding.jsonl", build_embedding_records(all_index))
        write_jsonl(out_dir / "context.jsonl", build_context_records(all_details))
    if incremental or rebuild:
        save_incremental_cache(cache_file, next_cache)
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Fertig: {out_dir}")
    print(f"Dateien: {len(manifest_files)}")
    print(f"Index Records: {len(all_index)}")
    print(f"Detail Records: {len(all_details)}")
    print(f"Relation Records: {len(all_relations)}")
