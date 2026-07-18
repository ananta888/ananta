from __future__ import annotations

import base64
import zlib
from urllib.parse import quote

import pytest
from rag_helper.extractors.diagram_extractor import DiagramExtractor, DrawioExtractor
from rag_helper.extractors.xml_security import XmlSecurityError


@pytest.mark.parametrize(
    ("rel_path", "source", "expected_nodes", "expected_edges", "diagram_kind"),
    [
        ("flow.mmd", "flowchart TD\nA[Start] --> B[Finish]\n", 2, 1, "flowchart"),
        ("sequence.mermaid", "sequenceDiagram\nAlice->>Bob: Hello\nBob-->>Alice: Ack\n", 2, 2, "sequencediagram"),
        ("classes.mmd", "classDiagram\nAnimal <|-- Duck\n", 2, 1, "classdiagram"),
    ],
)
def test_mermaid_golden_diagrams_emit_nodes_edges_and_positions(
    rel_path: str,
    source: str,
    expected_nodes: int,
    expected_edges: int,
    diagram_kind: str,
) -> None:
    index, details, relations, stats = DiagramExtractor().parse(rel_path, source)
    assert index[0]["summary"]["diagram_kind"] == diagram_kind
    assert stats["node_count"] == expected_nodes
    assert stats["edge_count"] == expected_edges
    assert all(record.get("line", 1) >= 1 for record in details)
    assert all(
        record["trust_level"] == "inferred"
        and record["verification_status"] == "unverified"
        for record in details
        if record.get("kind") != "diagnostic"
    )
    assert all(relation["target_resolved"] for relation in relations if relation["relation"] == "diagram_edge")


def test_plantuml_component_edges_and_include_policy_are_static_and_repository_bounded() -> None:
    index, details, relations, stats = DiagramExtractor().parse(
        "architecture/components.puml",
        (
            "@startuml\n"
            '!include "shared/theme.puml"\n'
            "!include ../../outside.puml\n"
            "!include https://example.invalid/remote.puml\n"
            'component "Web" as web\n'
            'component "API" as api\n'
            "web --> api : HTTPS\n"
            "@enduml\n"
        ),
    )
    assert index[0]["summary"]["node_count"] == 2
    assert stats["edge_count"] == 1
    safe_include = next(item for item in relations if item["relation"] == "includes_diagram")
    assert safe_include["target"] == "shared/theme.puml"
    assert safe_include["include_read"] is False
    blocked = [item for item in details if item.get("code") == "plantuml_include_outside_repository"]
    assert len(blocked) == 2
    assert all(item["severity"] == "security" for item in blocked)


def test_graphviz_dot_extracts_subgraphs_labels_and_edges() -> None:
    index, details, relations, stats = DiagramExtractor().parse(
        "architecture/system.dot",
        (
            "digraph System {\n"
            "  subgraph cluster_backend {\n"
            '    api [label="API"];\n'
            '    db [label="Database"];\n'
            "  }\n"
            '  api -> db [label="SQL"];\n'
            "}\n"
        ),
    )
    assert index[0]["summary"]["group_count"] == 1
    assert stats["node_count"] == 2
    edge = next(item for item in relations if item["relation"] == "diagram_edge")
    assert edge["label"] == "SQL"
    assert edge["resolution_status"] == "resolved"
    assert any(item["kind"] == "dot_subgraph" and item["name"] == "cluster_backend" for item in details)


def test_diagram_syntax_errors_keep_partial_index_and_positioned_diagnostic() -> None:
    index, details, _, stats = DiagramExtractor().parse("broken.dot", "digraph G {\na -> b;\n")
    diagnostic = next(item for item in details if item.get("code") == "dot_unbalanced_braces")
    assert index[0]["parser_mode"] == "static_diagram_lexer"
    assert diagnostic["line"] == 2
    assert diagnostic["fallback"] == "partial_declaration_index"
    assert stats["diagnostic_count"] == 1


def _compressed_drawio_page(xml: str) -> str:
    compressor = zlib.compressobj(level=9, wbits=-15)
    payload = compressor.compress(quote(xml, safe="~()*!.'").encode("utf-8")) + compressor.flush()
    return base64.b64encode(payload).decode("ascii")


def test_drawio_decodes_bounded_compressed_pages_and_resolves_edges() -> None:
    inner = (
        "<mxGraphModel><root>"
        '<mxCell id="0"/>'
        '<mxCell id="1" parent="0"/>'
        '<mxCell id="api" value="&lt;b&gt;API&lt;/b&gt;" vertex="1" parent="1"/>'
        '<mxCell id="db" value="Database" vertex="1" parent="1"/>'
        '<mxCell id="edge" value="SQL" edge="1" source="api" target="db" parent="1"/>'
        "</root></mxGraphModel>"
    )
    payload = _compressed_drawio_page(inner)
    index, details, relations, stats = DrawioExtractor(max_decoded_page_size_kb=64).parse(
        "architecture/system.drawio",
        f'<mxfile><diagram name="System">{payload}</diagram></mxfile>',
    )
    assert index[0]["summary"]["decoded_page_count"] == 1
    assert stats["failed_page_count"] == 0
    assert {item["name"] for item in details if item["kind"] == "drawio_node"} >= {"API", "Database"}
    edge = next(item for item in relations if item["relation"] == "diagram_edge")
    assert edge["label"] == "SQL"
    assert edge["resolution_status"] == "resolved"
    assert edge["verification_status"] == "verified"
    assert all(
        item["verification_status"] == "verified"
        for item in details
        if item["kind"] in {"drawio_page", "drawio_node"}
    )


def test_drawio_invalid_compressed_content_reports_failure_instead_of_fake_success() -> None:
    index, details, relations, stats = DrawioExtractor().parse(
        "architecture/broken.drawio",
        '<mxfile><diagram name="Broken">not-valid-base64!</diagram></mxfile>',
    )
    assert index[0]["summary"]["failed_page_count"] == 1
    assert stats["node_count"] == 0
    assert relations == []
    assert any(item.get("code") == "drawio_page_decode_failed" for item in details)


def test_drawio_encrypted_and_decoded_budget_exceeded_have_stable_reason_codes() -> None:
    _, encrypted_details, encrypted_relations, encrypted_stats = DrawioExtractor().parse(
        "architecture/encrypted.drawio",
        '<mxfile encrypted="true"><diagram name="Encrypted">opaque</diagram></mxfile>',
    )
    assert encrypted_relations == []
    assert encrypted_stats["failed_page_count"] == 1
    assert any(
        item.get("code") == "drawio_encrypted_content_unsupported"
        for item in encrypted_details
    )

    oversized_inner = (
        "<mxGraphModel><root><mxCell id=\"0\"/>"
        f'<mxCell id="large" value="{"x" * 4_096}" vertex="1"/>'
        "</root></mxGraphModel>"
    )
    payload = _compressed_drawio_page(oversized_inner)
    _, budget_details, budget_relations, budget_stats = DrawioExtractor(
        max_decoded_page_size_kb=1
    ).parse(
        "architecture/oversized.drawio",
        f'<mxfile><diagram name="Oversized">{payload}</diagram></mxfile>',
    )
    assert budget_relations == []
    assert budget_stats["failed_page_count"] == 1
    assert any(
        item.get("code") == "drawio_decoded_page_budget_exceeded"
        for item in budget_details
    )


def test_diagram_hidden_unicode_is_rejected_before_structural_inference() -> None:
    index, details, relations, stats = DiagramExtractor().parse(
        "architecture/hidden.mmd",
        "flowchart TD\nA[Visible]\u202e --> B[Hidden]\n",
    )
    assert index[0]["parser_mode"] == "static_diagram_lexer_rejected"
    assert relations == []
    assert stats["node_count"] == 0
    assert details[0]["code"] == "diagram_hidden_unicode_forbidden"
    assert details[0]["severity"] == "security"


@pytest.mark.parametrize(
    ("rel_path", "source", "reason_code"),
    [
        ("architecture/malformed.mmd", "A[Guessed] --> B[Guessed]\n", "mermaid_unknown_diagram_declaration"),
        ("architecture/malformed.puml", "component API\nAPI --> DB\n", "plantuml_boundary_missing"),
    ],
)
def test_malformed_text_diagrams_do_not_publish_guessed_graph_records(
    rel_path: str,
    source: str,
    reason_code: str,
) -> None:
    index, details, relations, stats = DiagramExtractor().parse(rel_path, source)
    assert index[0]["summary"]["node_count"] == 0
    assert stats["edge_count"] == 0
    assert relations == []
    assert [record.get("code") for record in details] == [reason_code]


def test_plantuml_sequence_participants_and_message_are_deterministic() -> None:
    index, details, relations, stats = DiagramExtractor().parse(
        "architecture/login.puml",
        (
            "@startuml\n"
            "participant Browser\n"
            "participant API\n"
            "Browser -> API : login\n"
            "@enduml\n"
        ),
    )
    assert index[0]["summary"]["node_count"] == 2
    assert {record["name"] for record in details if record["kind"] == "plantuml_node"} == {
        "Browser",
        "API",
    }
    edge = next(record for record in relations if record["relation"] == "diagram_edge")
    assert edge["label"] == "login"
    assert edge["resolution_status"] == "resolved"
    assert stats["edge_count"] == 1


def test_drawio_reuses_fail_closed_xml_policy_for_dtd_and_entities() -> None:
    with pytest.raises(XmlSecurityError, match="xml_dtd_or_entity_declaration_forbidden"):
        DrawioExtractor().parse(
            "architecture/unsafe.drawio",
            '<!DOCTYPE mxfile [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<mxfile><diagram name="Unsafe">&xxe;</diagram></mxfile>',
        )
