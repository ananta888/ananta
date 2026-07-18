from __future__ import annotations

import time
from pathlib import Path

from codecompass_rag import DEFAULT_EXTENSIONS
from rag_helper.application.document_extractor import build_extractors, process_snapshot
from rag_helper.application.file_scanner import FileSnapshot
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.extractors.adoc_extractor import AdocExtractor
from rag_helper.extractors.adoc_provenance_adapter import AdocProvenanceAdapter
from rag_helper.extractors.angular_asset_extractor import AngularTemplateExtractor, StylesheetExtractor
from rag_helper.extractors.configuration_extractor import ConfigurationExtractor
from rag_helper.extractors.data_contract_extractor import JsonDocumentExtractor, SqlExtractor
from rag_helper.extractors.diagram_extractor import DiagramExtractor, DrawioExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor
from rag_helper.extractors.infrastructure_extractor import BuildScriptExtractor, YamlInfrastructureExtractor
from rag_helper.extractors.script_extractor import PowerShellExtractor, ShellScriptExtractor
from rag_helper.extractors.tabular_notebook_extractor import DelimitedTextExtractor, NotebookExtractor


class _DummyJava:
    def __init__(self, **_kwargs) -> None:
        pass


class _DummyXml:
    def __init__(self, **_kwargs) -> None:
        pass


def _build() -> dict[str, object]:
    return build_extractors(
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=ProcessingLimits(embedding_text_mode="compact"),
        java_extractor_cls=_DummyJava,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=_DummyXml,
        xsd_extractor_cls=_DummyXml,
    )


def test_factory_registers_every_structured_format_family_without_legacy_text_dependency() -> None:
    extractors = _build()

    assert isinstance(extractors["adoc"], AdocProvenanceAdapter)
    assert all(isinstance(extractors[ext], DocumentationExtractor) for ext in ("md", "mdx", "rst"))
    assert all(
        isinstance(extractors[ext], ConfigurationExtractor) for ext in ("toml", "ini", "cfg", "conf", "properties")
    )
    assert all(isinstance(extractors[ext], YamlInfrastructureExtractor) for ext in ("yaml", "yml"))
    assert isinstance(extractors["html"], AngularTemplateExtractor)
    assert all(isinstance(extractors[ext], StylesheetExtractor) for ext in ("css", "scss", "sass", "less"))
    assert all(isinstance(extractors[ext], BuildScriptExtractor) for ext in ("", "mk", "makefile", "jenkinsfile"))
    assert all(isinstance(extractors[ext], ShellScriptExtractor) for ext in ("sh", "bash", "zsh", "fish"))
    assert all(isinstance(extractors[ext], PowerShellExtractor) for ext in ("ps1", "psm1"))
    assert isinstance(extractors["sql"], SqlExtractor)
    assert isinstance(extractors["json"], JsonDocumentExtractor)
    assert all(isinstance(extractors[ext], DelimitedTextExtractor) for ext in ("csv", "tsv"))
    assert isinstance(extractors["ipynb"], NotebookExtractor)
    assert all(
        isinstance(extractors[ext], DiagramExtractor) for ext in ("mmd", "mermaid", "puml", "plantuml", "dot", "gv")
    )
    assert isinstance(extractors["drawio"], DrawioExtractor)


def test_default_extension_set_reaches_all_registered_format_dispatch_keys() -> None:
    expected = {
        "",
        "adoc",
        "md",
        "mdx",
        "rst",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "properties",
        "html",
        "css",
        "scss",
        "sass",
        "less",
        "dockerfile",
        "makefile",
        "jenkinsfile",
        "mk",
        "sh",
        "bash",
        "zsh",
        "fish",
        "ps1",
        "psm1",
        "json",
        "sql",
        "proto",
        "graphql",
        "gql",
        "tf",
        "tfvars",
        "csv",
        "tsv",
        "ipynb",
        "mmd",
        "mermaid",
        "puml",
        "plantuml",
        "dot",
        "gv",
        "drawio",
    }
    assert expected <= DEFAULT_EXTENSIONS


def test_factory_passes_processing_limits_to_format_extractors() -> None:
    limits = ProcessingLimits(
        max_parser_records_per_file=17,
        max_parser_depth=7,
        max_document_code_block_chars=123,
        max_yaml_aliases=3,
        max_yaml_nodes=29,
        max_notebook_cells=5,
        max_notebook_cell_chars=127,
        max_tabular_rows=11,
        max_tabular_sample_rows=4,
        max_tabular_columns=9,
        max_tabular_cell_chars=33,
        max_drawio_decoded_page_size_kb=64,
    )
    extractors = build_extractors(
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=limits,
        java_extractor_cls=_DummyJava,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=_DummyXml,
        xsd_extractor_cls=_DummyXml,
    )
    assert extractors["md"].max_code_block_chars == 123
    assert extractors["md"].max_records == 17
    assert extractors["yaml"].config_extractor.max_aliases == 3
    assert extractors["yaml"].config_extractor.max_nodes == 29
    assert extractors["yaml"].config_extractor.max_depth == 7
    assert extractors["ipynb"].max_cells == 5
    assert extractors["ipynb"].max_cell_chars == 127
    assert extractors["csv"].max_rows == 11
    assert extractors["csv"].sample_rows == 4
    assert extractors["csv"].max_columns == 9
    assert extractors["csv"].max_cell_chars == 33
    assert extractors["drawio"].max_decoded_page_size_kb == 64


def test_process_snapshot_enforces_global_parser_line_limit_before_dispatch() -> None:
    snapshot = FileSnapshot(
        path=Path("docs/large.md"),
        rel_path="docs/large.md",
        ext="md",
        text="one\ntwo\nthree\n",
        size=14,
        sha1="known-test-hash",
    )
    result = process_snapshot(
        snapshot=snapshot,
        options_signature="options",
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=ProcessingLimits(max_parser_lines=2),
        java_extractor_cls=_DummyJava,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=_DummyXml,
        xsd_extractor_cls=_DummyXml,
        known_package_types={},
        known_namespace_types={},
    )
    assert result.index == []
    assert result.manifest_entry["skip_reason"] == "max_parser_lines_exceeded"
    assert result.manifest_entry["observed_line_count"] == 3


def test_process_snapshot_enforces_exact_byte_and_timeout_limits(monkeypatch) -> None:
    oversized = FileSnapshot(
        path=Path("docs/oversized.md"),
        rel_path="docs/oversized.md",
        ext="md",
        text="# oversized\n",
        size=12,
        sha1="oversized",
    )
    oversized_result = process_snapshot(
        snapshot=oversized,
        options_signature="options",
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=ProcessingLimits(max_file_size_kb=None, max_file_size_bytes=8),
        java_extractor_cls=_DummyJava,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=_DummyXml,
        xsd_extractor_cls=_DummyXml,
        known_package_types={},
        known_namespace_types={},
    )
    assert oversized_result.manifest_entry["skip_reason"] == "max_file_size_exceeded"
    assert oversized_result.manifest_entry["limit_bytes"] == 8

    original_parse = DocumentationExtractor.parse

    def slow_parse(self, rel_path: str, text: str):
        time.sleep(0.005)
        return original_parse(self, rel_path, text)

    monkeypatch.setattr(DocumentationExtractor, "parse", slow_parse)
    timed = FileSnapshot(
        path=Path("docs/slow.md"),
        rel_path="docs/slow.md",
        ext="md",
        text="# slow\n",
        size=7,
        sha1="slow",
    )
    timed_result = process_snapshot(
        snapshot=timed,
        options_signature="options",
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=ProcessingLimits(parser_timeout_ms=1),
        java_extractor_cls=_DummyJava,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=_DummyXml,
        xsd_extractor_cls=_DummyXml,
        known_package_types={},
        known_namespace_types={},
    )
    assert timed_result.index == []
    assert timed_result.manifest_entry["skip_reason"] == "parser_timeout"


def test_adoc_adapter_preserves_established_records_and_adds_line_provenance_links_and_lists() -> None:
    extractor = _build()["adoc"]
    index, details, relations, stats = extractor.parse(
        "docs/guide.adoc",
        (
            "= Guide\n"
            ":toc:\n\n"
            "== Intro\n"
            "* item\n"
            "xref:other.adoc[Other]\n\n"
            "[source,python]\n"
            "----\n"
            "raise RuntimeError('inert')\n"
            "----\n"
        ),
    )
    section = next(item for item in index if item["kind"] == "adoc_section")
    code = next(item for item in details if item["kind"] == "adoc_code_block")
    assert section["line"] == 4
    assert code["executed"] is False
    assert code["line"] == 10
    assert any(item["kind"] == "adoc_document_attribute" and item["line"] == 2 for item in details)
    assert any(item["kind"] == "adoc_list_item" and item["line"] == 5 for item in details)
    assert any(item["kind"] == "adoc_link" and item["line"] == 6 for item in details)
    assert any(item["relation"] == "references_document" and item["target"] == "other.adoc" for item in relations)
    assert stats["provenance_adapter"] is True
