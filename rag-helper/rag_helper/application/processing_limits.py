from __future__ import annotations

from dataclasses import asdict, dataclass

from rag_helper.domain.xml_security import (
    DEFAULT_MAX_XML_ATTRIBUTES,
    DEFAULT_MAX_XML_DEPTH,
    DEFAULT_MAX_XML_INPUT_SIZE_KB,
    DEFAULT_MAX_XML_NODES,
)


@dataclass(frozen=True)
class ProcessingLimits:
    # Omitted configuration must not turn repository reads into an unbounded
    # operation. Callers can still select another positive ceiling explicitly.
    max_file_size_kb: int | None = 1024
    max_file_size_bytes: int | None = None
    max_parser_lines: int | None = 200_000
    parser_timeout_ms: int | None = 2_000
    max_parser_records_per_file: int = 10_000
    max_parser_depth: int = 64
    max_document_code_block_chars: int = 16_000
    max_yaml_aliases: int = 50
    max_yaml_nodes: int = 20_000
    max_notebook_cells: int = 2_000
    max_notebook_cell_chars: int = 32_000
    max_notebook_output_bytes: int = 0
    max_tabular_rows: int = 2_000
    max_tabular_sample_rows: int = 100
    max_tabular_columns: int = 500
    max_tabular_cell_chars: int = 16_000
    max_drawio_decoded_page_size_kb: int = 5 * 1024
    max_xml_nodes: int | None = DEFAULT_MAX_XML_NODES
    max_xml_input_size_kb: int | None = DEFAULT_MAX_XML_INPUT_SIZE_KB
    max_xml_depth: int | None = DEFAULT_MAX_XML_DEPTH
    max_xml_attributes: int | None = DEFAULT_MAX_XML_ATTRIBUTES
    oversized_xml_fallback: bool = False
    max_methods_per_class: int | None = None
    max_records_per_file: int | None = None
    max_relation_records_per_file: int | None = None
    max_workers: int = 1
    xml_mode: str = "all"
    xml_index_mode: str = "tags"
    xml_relation_mode: str = "per-node"
    xml_repetitive_child_threshold: int = 25
    java_relation_mode: str = "full"
    java_detail_mode: str = "full"
    generated_code_mode: str = "mark"
    generated_comment_markers: tuple[str, ...] = ()
    resolve_wildcard_imports: bool = True
    mark_import_conflicts: bool = True
    resolve_method_targets: bool = True
    resolve_framework_relations: bool = True
    embedding_text_mode: str = "verbose"
    retrieval_output_mode: str = "legacy"
    context_output_mode: str = "full"
    output_compaction_mode: str = "off"
    gem_partition_mode: str = "off"
    xml_overview_mode: str = "off"
    manifest_output_mode: str = "full"
    relation_output_mode: str = "combined"
    output_partition_mode: str = "off"
    importance_scoring_mode: str = "basic"
    graph_export_mode: str = "off"
    benchmark_mode: str = "off"
    duplicate_detection_mode: str = "off"
    specialized_chunker_mode: str = "off"
    output_bundle_mode: str = "off"
    domain_discovery_mode: str = "off"
    domain_descriptor_suggestions: bool = False
    llm_narrative_endpoint: str | None = None
    llm_narrative_model: str | None = None
    md_heading_chunk_level: int = 2
    md_max_block_size_chars: int = 2000
    md_min_block_size_chars: int = 50
    md_max_headings_per_note: int | None = None
    md_max_links_per_note: int | None = 200
    canvas_max_nodes: int | None = None

    def as_options(self) -> dict[str, object]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }
