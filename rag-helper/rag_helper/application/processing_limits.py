from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProcessingLimits:
    max_file_size_kb: int | None = None
    max_xml_nodes: int | None = None
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

    def as_options(self) -> dict[str, int | str]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }
