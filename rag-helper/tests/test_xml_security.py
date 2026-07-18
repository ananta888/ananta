from __future__ import annotations

import json

import pytest
from rag_helper.application.document_extractor import build_extractors
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project
from rag_helper.extractors.xml_extractor import XmlExtractor
from rag_helper.extractors.xml_security import XmlSecurityError
from rag_helper.extractors.xsd_extractor import XsdExtractor


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_reject_external_entities(extractor_cls) -> None:
    payload = """<!DOCTYPE root [
      <!ENTITY external SYSTEM "file:///etc/passwd">
    ]><root>&external;</root>"""

    with pytest.raises(XmlSecurityError, match="xml_dtd_or_entity_declaration_forbidden"):
        extractor_cls().parse("untrusted.xml", payload)


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_reject_internal_entity_expansion(extractor_cls) -> None:
    payload = """<!DOCTYPE root [
      <!ENTITY a "1234567890">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">
    ]><root>&b;</root>"""

    with pytest.raises(XmlSecurityError, match="xml_dtd_or_entity_declaration_forbidden"):
        extractor_cls().parse("expansion.xml", payload)


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_fail_closed_on_invalid_xml(extractor_cls) -> None:
    with pytest.raises(XmlSecurityError, match="xml_syntax_error"):
        extractor_cls().parse("broken.xml", "<root><open></root>")


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_enforce_depth_limit(extractor_cls) -> None:
    with pytest.raises(XmlSecurityError, match="max_xml_depth_exceeded"):
        extractor_cls(max_xml_depth=2).parse(
            "deep.xml",
            "<root><level-one><level-two /></level-one></root>",
        )


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_enforce_input_size_limit(extractor_cls) -> None:
    payload = f"<root>{'x' * 2048}</root>"

    with pytest.raises(XmlSecurityError, match="max_xml_input_size_kb_exceeded"):
        extractor_cls(max_xml_input_size_kb=1).parse("large.xml", payload)


@pytest.mark.parametrize("extractor_cls", [XmlExtractor, XsdExtractor])
def test_xml_extractors_enforce_aggregate_attribute_limit(extractor_cls) -> None:
    with pytest.raises(XmlSecurityError, match="max_xml_attributes_exceeded"):
        extractor_cls(max_xml_attributes=2).parse(
            "attributes.xml",
            "<root first='1'><child second='2' third='3' /></root>",
        )


def test_processing_limits_are_forwarded_to_both_xml_extractors() -> None:
    class _Noop:
        def __init__(self, **_kwargs) -> None:
            pass

    limits = ProcessingLimits(
        max_xml_nodes=7,
        max_xml_input_size_kb=8,
        max_xml_depth=9,
        max_xml_attributes=10,
    )
    extractors = build_extractors(
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=limits,
        java_extractor_cls=_Noop,
        adoc_extractor_cls=_Noop,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
    )

    for extension in ("xml", "xsd"):
        extractor = extractors[extension]
        assert extractor.max_xml_nodes == 7
        assert extractor.max_xml_input_size_kb == 8
        assert extractor.max_xml_depth == 9
        assert extractor.max_xml_attributes == 10


def test_valid_xml_extraction_reports_observed_resource_usage() -> None:
    _, _, _, stats = XmlExtractor().parse(
        "valid.xml",
        "<root id='1'><child name='safe' /></root>",
    )

    assert stats["node_count"] == 2
    assert stats["max_depth"] == 2
    assert stats["attribute_count"] == 2


def test_security_rejection_is_persisted_as_diagnostic_not_success(tmp_path) -> None:
    class _Noop:
        def __init__(self, **_kwargs) -> None:
            pass

    root = tmp_path / "source"
    output = tmp_path / "output"
    root.mkdir()
    (root / "unsafe.xml").write_text(
        '<!DOCTYPE root SYSTEM "https://example.invalid/external.dtd"><root />',
        encoding="utf-8",
    )

    process_project(
        root=root,
        out_dir=output,
        extensions={"xml"},
        excludes=set(),
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        include_globs=[],
        exclude_globs=[],
        limits=ProcessingLimits(),
        java_extractor_cls=_Noop,
        adoc_extractor_cls=_Noop,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["index_record_count"] == 0
    assert manifest["error_count"] == 1
    assert manifest["files"][0]["error"] == "xml_dtd_or_entity_declaration_forbidden"
