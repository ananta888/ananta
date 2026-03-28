from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProcessingLimits:
    max_file_size_kb: int | None = None
    max_xml_nodes: int | None = None
    max_methods_per_class: int | None = None
    max_records_per_file: int | None = None
    max_workers: int = 1
    xml_mode: str = "all"
    xml_repetitive_child_threshold: int = 25
    generated_code_mode: str = "mark"
    generated_comment_markers: tuple[str, ...] = ()
    resolve_wildcard_imports: bool = True
    mark_import_conflicts: bool = True
    resolve_method_targets: bool = True
    resolve_framework_relations: bool = True
    embedding_text_mode: str = "verbose"
    retrieval_output_mode: str = "legacy"
    importance_scoring_mode: str = "basic"

    def as_options(self) -> dict[str, int | str]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }
