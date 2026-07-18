"""Per-file extraction with ProcessingLimits integration (SPLIT-033).

Extracted from project_processor.py to keep the orchestration module
focused on pipeline control. This module owns the per-file processing
responsibilities:

  - Extractor-Factory: build_extractor / build_extractors
  - Per-Snapshot-Extraktion: process_snapshot (mit allen Limit-Branches)
  - Oversized-XML-Fallback
  - Cache-Entry-Bau
  - Generated-Code-Annotation und Progress-Emission

All functions take their dependencies explicitly (java_extractor_cls,
ProcessingLimits, etc.) - no module-level state. The cache checkpoint
helper is the only side-effecting utility and is isolated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from rag_helper.application.file_scanner import FileSnapshot
from rag_helper.application.generated_code import detect_generated_code
from rag_helper.application.importance_scoring import score_index_records
from rag_helper.application.incremental_cache import save_incremental_cache
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.relation_compaction import compact_relation_records
from rag_helper.extractors.adoc_provenance_adapter import AdocProvenanceAdapter
from rag_helper.extractors.angular_asset_extractor import (
    AngularTemplateExtractor,
    StylesheetExtractor,
)
from rag_helper.extractors.base import FileSkipped
from rag_helper.extractors.configuration_extractor import ConfigurationExtractor
from rag_helper.extractors.data_contract_extractor import (
    GraphqlExtractor,
    JsonDocumentExtractor,
    ProtoExtractor,
    SqlExtractor,
    TerraformExtractor,
)
from rag_helper.extractors.diagram_extractor import DiagramExtractor, DrawioExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor
from rag_helper.extractors.infrastructure_extractor import (
    BuildScriptExtractor,
    DockerfileExtractor,
    YamlInfrastructureExtractor,
)
from rag_helper.extractors.script_extractor import PowerShellExtractor, ShellScriptExtractor
from rag_helper.extractors.tabular_notebook_extractor import (
    DelimitedTextExtractor,
    NotebookExtractor,
)
from rag_helper.utils.ids import sha1_text


@dataclass(frozen=True)
class FileProcessingResult:
    rel_path: str
    index: list[dict]
    details: list[dict]
    relations: list[dict]
    manifest_entry: dict
    cache_entry: dict


def build_extractor(extractor_cls, **kwargs):
    try:
        return extractor_cls(**kwargs)
    except TypeError:
        return extractor_cls()


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
    csharp_extractor_cls=None,
    obsidian_extractor_cls=None,
    obsidian_canvas_extractor_cls=None,
    obsidian_vault_name: str = "default",
    n8n_extractor_cls=None,
    teaching_extractor_cls=None,
) -> dict[str, object]:
    adoc_extractor = build_extractor(
        adoc_extractor_cls, embedding_text_mode=limits.embedding_text_mode
    )
    extractors = {
        "java": java_extractor_cls(
            include_code_snippets=include_code_snippets,
            exclude_trivial_methods=exclude_trivial_methods,
            max_methods_per_class=limits.max_methods_per_class,
            detail_mode=limits.java_detail_mode,
            relation_mode=limits.java_relation_mode,
            resolve_wildcard_imports=limits.resolve_wildcard_imports,
            mark_import_conflicts=limits.mark_import_conflicts,
            resolve_method_targets=limits.resolve_method_targets,
            resolve_framework_relations=limits.resolve_framework_relations,
            embedding_text_mode=limits.embedding_text_mode,
        ),
        "adoc": AdocProvenanceAdapter(
            adoc_extractor, embedding_text_mode=limits.embedding_text_mode
        ),
        "xml": build_extractor(
            xml_extractor_cls,
            include_xml_node_details=include_xml_node_details,
            max_xml_nodes=limits.max_xml_nodes,
            max_xml_input_size_kb=limits.max_xml_input_size_kb,
            max_xml_depth=limits.max_xml_depth,
            max_xml_attributes=limits.max_xml_attributes,
            xml_mode=limits.xml_mode,
            index_mode=limits.xml_index_mode,
            relation_mode=limits.xml_relation_mode,
            repetitive_child_threshold=limits.xml_repetitive_child_threshold,
            embedding_text_mode=limits.embedding_text_mode,
        ),
        "xsd": build_extractor(
            xsd_extractor_cls,
            max_xml_nodes=limits.max_xml_nodes,
            max_xml_input_size_kb=limits.max_xml_input_size_kb,
            max_xml_depth=limits.max_xml_depth,
            max_xml_attributes=limits.max_xml_attributes,
            embedding_text_mode=limits.embedding_text_mode,
        ),
    }
    if csharp_extractor_cls is not None:
        extractors["cs"] = csharp_extractor_cls(
            include_code_snippets=include_code_snippets,
            exclude_trivial_methods=exclude_trivial_methods,
            max_methods_per_class=limits.max_methods_per_class,
            detail_mode=limits.java_detail_mode,
            relation_mode=limits.java_relation_mode,
            mark_import_conflicts=limits.mark_import_conflicts,
            resolve_method_targets=limits.resolve_method_targets,
            embedding_text_mode=limits.embedding_text_mode,
        )
    if text_extractor_cls is not None:
        text_extractor = build_extractor(
            text_extractor_cls, embedding_text_mode=limits.embedding_text_mode
        )
        extractors.update(
            {
                "properties": text_extractor,
                "yaml": text_extractor,
                "yml": text_extractor,
                "sql": text_extractor,
                "md": text_extractor,
                "py": text_extractor,
                "ts": text_extractor,
                "tsx": text_extractor,
                "gradle": text_extractor,
                "kts": text_extractor,
            }
        )

    # Capability-specific extractors take precedence over the legacy text
    # outline.  Each parser is static and records honest fallback diagnostics;
    # none render templates, evaluate expressions or execute scripts.
    documentation_extractor = DocumentationExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_code_block_chars=limits.max_document_code_block_chars,
        max_records=limits.max_parser_records_per_file,
    )
    configuration_extractor = ConfigurationExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_aliases=limits.max_yaml_aliases,
        max_nodes=limits.max_yaml_nodes,
        max_depth=limits.max_parser_depth,
        max_records=limits.max_parser_records_per_file,
    )
    yaml_extractor = YamlInfrastructureExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_aliases=limits.max_yaml_aliases,
        max_nodes=limits.max_yaml_nodes,
        max_depth=limits.max_parser_depth,
        max_records=limits.max_parser_records_per_file,
    )
    stylesheet_extractor = StylesheetExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_records=limits.max_parser_records_per_file,
    )
    shell_extractor = ShellScriptExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_records=limits.max_parser_records_per_file,
    )
    powershell_extractor = PowerShellExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_records=limits.max_parser_records_per_file,
    )
    diagram_extractor = DiagramExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_records=limits.max_parser_records_per_file,
    )
    build_script_extractor = BuildScriptExtractor(
        embedding_text_mode=limits.embedding_text_mode,
        max_records=limits.max_parser_records_per_file,
        max_docker_instructions=limits.max_parser_records_per_file,
    )
    extractors.update(
        {
            "md": documentation_extractor,
            "mdx": documentation_extractor,
            "rst": documentation_extractor,
            "yaml": yaml_extractor,
            "yml": yaml_extractor,
            "toml": configuration_extractor,
            "ini": configuration_extractor,
            "cfg": configuration_extractor,
            "conf": configuration_extractor,
            "properties": configuration_extractor,
            "html": AngularTemplateExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_records=limits.max_parser_records_per_file,
            ),
            "css": stylesheet_extractor,
            "scss": stylesheet_extractor,
            "sass": stylesheet_extractor,
            "less": stylesheet_extractor,
            "dockerfile": DockerfileExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_instructions=limits.max_parser_records_per_file,
            ),
            "mk": build_script_extractor,
            "makefile": build_script_extractor,
            "jenkinsfile": build_script_extractor,
            "": build_script_extractor,
            "sh": shell_extractor,
            "bash": shell_extractor,
            "zsh": shell_extractor,
            "fish": shell_extractor,
            "ps1": powershell_extractor,
            "psm1": powershell_extractor,
            "sql": SqlExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_statements=limits.max_parser_records_per_file,
            ),
            "proto": ProtoExtractor(embedding_text_mode=limits.embedding_text_mode),
            "graphql": GraphqlExtractor(embedding_text_mode=limits.embedding_text_mode),
            "gql": GraphqlExtractor(embedding_text_mode=limits.embedding_text_mode),
            "tf": TerraformExtractor(embedding_text_mode=limits.embedding_text_mode),
            "tfvars": TerraformExtractor(embedding_text_mode=limits.embedding_text_mode),
            "csv": DelimitedTextExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_rows=limits.max_tabular_rows,
                sample_rows=limits.max_tabular_sample_rows,
                max_columns=limits.max_tabular_columns,
                max_cell_chars=limits.max_tabular_cell_chars,
            ),
            "tsv": DelimitedTextExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_rows=limits.max_tabular_rows,
                sample_rows=limits.max_tabular_sample_rows,
                max_columns=limits.max_tabular_columns,
                max_cell_chars=limits.max_tabular_cell_chars,
            ),
            "ipynb": NotebookExtractor(
                embedding_text_mode=limits.embedding_text_mode,
                max_cells=limits.max_notebook_cells,
                max_cell_chars=limits.max_notebook_cell_chars,
            ),
            "mmd": diagram_extractor,
            "mermaid": diagram_extractor,
            "puml": diagram_extractor,
            "plantuml": diagram_extractor,
            "dot": diagram_extractor,
            "gv": diagram_extractor,
            "drawio": DrawioExtractor(
                max_xml_nodes=limits.max_xml_nodes,
                max_xml_input_size_kb=limits.max_xml_input_size_kb,
                max_xml_depth=limits.max_xml_depth,
                max_xml_attributes=limits.max_xml_attributes,
                max_decoded_page_size_kb=limits.max_drawio_decoded_page_size_kb,
                embedding_text_mode=limits.embedding_text_mode,
            ),
        }
    )
    if obsidian_extractor_cls is not None:
        # ObsidianExtractor takes precedence over text_extractor for .md
        obs_extractor = obsidian_extractor_cls(
            vault_name=obsidian_vault_name,
            heading_chunk_level=limits.md_heading_chunk_level,
            max_block_size_chars=limits.md_max_block_size_chars,
            min_block_size_chars=limits.md_min_block_size_chars,
            max_links_per_note=limits.md_max_links_per_note,
            max_headings_per_note=limits.md_max_headings_per_note,
        )
        extractors["md"] = obs_extractor
    if obsidian_canvas_extractor_cls is not None:
        canvas_extractor = obsidian_canvas_extractor_cls(
            vault_name=obsidian_vault_name,
            max_nodes=limits.canvas_max_nodes,
        )
        extractors["canvas"] = canvas_extractor
    n8n_extractor = (
        build_extractor(n8n_extractor_cls, embedding_text_mode=limits.embedding_text_mode)
        if n8n_extractor_cls is not None
        else None
    )
    # JSON Schema is always recognized when JSON is in the configured file
    # set.  Non-schema JSON keeps the existing optional n8n behavior.
    extractors["json"] = JsonDocumentExtractor(
        fallback_extractor=n8n_extractor,
        embedding_text_mode=limits.embedding_text_mode,
        max_nodes=limits.max_parser_records_per_file,
        max_depth=limits.max_parser_depth,
    )
    if teaching_extractor_cls is not None:
        extractors["md"] = build_extractor(
            teaching_extractor_cls, embedding_text_mode=limits.embedding_text_mode
        )
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


def build_oversized_xml_fallback(
    snapshot: FileSnapshot,
    options_signature: str,
    limits: ProcessingLimits,
    pre_scan: dict | None = None,
) -> FileProcessingResult | None:
    if not limits.oversized_xml_fallback:
        return None
    if snapshot.ext not in {"xml", "xsd"}:
        return None
    if limits.max_xml_nodes is None or snapshot.text is None:
        return None

    rel_path = snapshot.rel_path
    lines = snapshot.text.splitlines()
    non_empty_lines = [line.strip() for line in lines if line.strip()]
    preview = " ".join(non_empty_lines[:8])[:240]
    sample_tags: list[str] = []
    seen_tags: set[str] = set()
    for line in non_empty_lines[:40]:
        for raw_tag in re.findall(r"<([A-Za-z_][\w:.-]*)", line):
            if raw_tag.startswith(("?", "!")):
                continue
            normalized = raw_tag.split(":", 1)[-1]
            if normalized in seen_tags:
                continue
            seen_tags.add(normalized)
            sample_tags.append(normalized)
            if len(sample_tags) >= 12:
                break
        if len(sample_tags) >= 12:
            break

    kind = f"{snapshot.ext}_oversized_summary"
    record_id = f"{kind}:{sha1_text(rel_path)}"
    index = [
        {
            "kind": kind,
            "file": rel_path,
            "id": record_id,
            "line_count": len(lines),
            "sample_tags": sample_tags,
            "summary": (
                f"Oversized {snapshot.ext.upper()} file summarized after max_xml_nodes limit. "
                f"Sample tags: {', '.join(sample_tags[:8]) or 'none'}."
            ),
            "embedding_text": (
                f"Oversized {snapshot.ext.upper()} file {rel_path}. "
                f"Sample tags {', '.join(sample_tags[:8]) or 'none'}. "
                f"Preview {preview or 'none'}."
            )[:320],
        }
    ]
    manifest_entry = {
        "file": rel_path,
        "ext": snapshot.ext,
        "sha1": snapshot.sha1,
        "size": snapshot.size,
        "fallback": True,
        "fallback_reason": "max_xml_nodes_exceeded",
        "cache_hit": False,
        "output_record_count": 1,
    }
    return FileProcessingResult(
        rel_path=rel_path,
        index=index,
        details=[],
        relations=[],
        manifest_entry=manifest_entry,
        cache_entry=build_cache_entry(
            snapshot, options_signature, manifest_entry, index, [], [], pre_scan
        ),
    )


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
    known_namespace_types: dict[str, set[str]],
    text_extractor_cls=None,
    pre_scan: dict | None = None,
    csharp_extractor_cls=None,
    obsidian_extractor_cls=None,
    obsidian_canvas_extractor_cls=None,
    obsidian_vault_name: str = "default",
    known_note_titles: dict | None = None,
    n8n_extractor_cls=None,
    teaching_extractor_cls=None,
) -> FileProcessingResult:
    started_at = perf_counter()
    rel_path = snapshot.rel_path
    ext = snapshot.ext
    file_size_bytes = snapshot.size

    file_size_ceilings = [
        value
        for value in (
            limits.max_file_size_kb * 1024
            if limits.max_file_size_kb is not None
            else None,
            limits.max_file_size_bytes,
        )
        if value is not None
    ]
    max_file_size_bytes = min(file_size_ceilings) if file_size_ceilings else None
    if max_file_size_bytes is not None and file_size_bytes > max_file_size_bytes:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "size": file_size_bytes,
            "skipped": True,
            "skip_reason": "max_file_size_exceeded",
            "limit_bytes": max_file_size_bytes,
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
        )

    text = snapshot.text
    if text is None:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "error": "unreadable",
            "cache_hit": False,
        }
        manifest_entry["duration_ms"] = round((perf_counter() - started_at) * 1000, 3)
        manifest_entry["output_record_count"] = 0
        return FileProcessingResult(
            rel_path=rel_path,
            index=[],
            details=[],
            relations=[],
            manifest_entry=manifest_entry,
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
        )

    line_count = len(text.splitlines())
    if limits.max_parser_lines is not None and line_count > limits.max_parser_lines:
        manifest_entry = {
            "file": rel_path,
            "ext": ext,
            "sha1": snapshot.sha1,
            "size": file_size_bytes,
            "skipped": True,
            "skip_reason": "max_parser_lines_exceeded",
            "observed_line_count": line_count,
            "limit": limits.max_parser_lines,
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
        )

    try:
        extractor = build_extractors(
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
            obsidian_extractor_cls=obsidian_extractor_cls,
            obsidian_canvas_extractor_cls=obsidian_canvas_extractor_cls,
            obsidian_vault_name=obsidian_vault_name,
            n8n_extractor_cls=n8n_extractor_cls,
            teaching_extractor_cls=teaching_extractor_cls,
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
                cache_entry=build_cache_entry(
                    snapshot, options_signature, manifest_entry, [], [], [], pre_scan
                ),
            )

        if ext == "java":
            idx, det, rel, stats = extractor.parse(
                rel_path=rel_path,
                text=text,
                known_package_types=known_package_types,
            )
        elif ext == "cs":
            idx, det, rel, stats = extractor.parse(
                rel_path=rel_path,
                text=text,
                known_namespace_types=known_namespace_types,
            )
        elif ext in {"md", "canvas"} and obsidian_extractor_cls is not None and not (
            ext == "md" and teaching_extractor_cls is not None
        ):
            # ObsidianExtractor: pass known_note_titles via known_package_types slot
            idx, det, rel, stats = extractor.parse(
                rel_path=rel_path,
                text=text,
                known_package_types=known_note_titles or {},
            )
        else:
            idx, det, rel, stats = extractor.parse(rel_path, text)

        elapsed_ms = (perf_counter() - started_at) * 1000
        if limits.parser_timeout_ms is not None and elapsed_ms > limits.parser_timeout_ms:
            manifest_entry = {
                "file": rel_path,
                "ext": ext,
                "sha1": snapshot.sha1,
                "size": file_size_bytes,
                "skipped": True,
                "skip_reason": "parser_timeout",
                "limit_ms": limits.parser_timeout_ms,
                "cache_hit": False,
                "duration_ms": round(elapsed_ms, 3),
                "output_record_count": 0,
            }
            return FileProcessingResult(
                rel_path=rel_path,
                index=[],
                details=[],
                relations=[],
                manifest_entry=manifest_entry,
                cache_entry=build_cache_entry(
                    snapshot,
                    options_signature,
                    manifest_entry,
                    [],
                    [],
                    [],
                    pre_scan,
                ),
            )

        rel, relation_compaction_stats = compact_relation_records(
            rel,
            max_relation_records_per_file=limits.max_relation_records_per_file,
        )
        if relation_compaction_stats:
            stats = dict(stats)
            stats["relation_compaction"] = relation_compaction_stats

        total_records = len(idx) + len(det) + len(rel)
        if (
            limits.max_records_per_file is not None
            and total_records > limits.max_records_per_file
        ):
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
                cache_entry=build_cache_entry(
                    snapshot, options_signature, manifest_entry, [], [], [], pre_scan
                ),
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, idx, det, rel, pre_scan
            ),
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
        )
    except Exception as exc:
        if ext in {"xml", "xsd"} and "max_xml_nodes_exceeded" in str(exc):
            fallback_result = build_oversized_xml_fallback(
                snapshot=snapshot,
                options_signature=options_signature,
                limits=limits,
                pre_scan=pre_scan,
            )
            if fallback_result is not None:
                fallback_result.manifest_entry["duration_ms"] = round(
                    (perf_counter() - started_at) * 1000, 3
                )
                return fallback_result
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
            cache_entry=build_cache_entry(
                snapshot, options_signature, manifest_entry, [], [], [], pre_scan
            ),
        )
