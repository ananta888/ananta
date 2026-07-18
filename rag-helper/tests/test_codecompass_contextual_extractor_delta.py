from __future__ import annotations

import json

from rag_helper.extractors.data_contract_extractor import JsonDocumentExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor
from rag_helper.extractors.text_file_extractor import TextFileExtractor


def test_markdown_tables_and_normalized_provenance_are_structural_and_stable() -> None:
    source = (
        "---\n"
        "token: never-copy-this\n"
        "---\n"
        "# Components\n"
        "\n"
        "| Name | Owner |\n"
        "| :--- | ---: |\n"
        "| API | secret-table-value |\n"
    )
    index, details, relations, stats = DocumentationExtractor().parse("docs/components.md", source)
    table = next(record for record in details if record["kind"] == "md_table")

    assert table["record_kind"] == "md_table"
    assert table["columns"] == ["Name", "Owner"]
    assert table["alignments"] == ["left", "right"]
    assert table["row_count"] == 1
    assert (table["line_start"], table["line_end"]) == (6, 8)
    assert len(table["content_hash"]) == 64
    assert table["evidence"] == {
        "path": "docs/components.md",
        "source_file": "docs/components.md",
        "source_kind": "tool_output",
        "source_record_id": table["record_id"],
        "extractor": "DocumentationExtractor",
        "line_start": 6,
        "line_end": 8,
        "content_hash": table["content_hash"],
        "verification_status": "unverified",
    }
    assert any(relation["relation"] == "contains_table" for relation in relations)
    assert stats["table_count"] == 1
    assert "never-copy-this" not in json.dumps((index, details, relations, stats))
    assert "secret-table-value" not in json.dumps((index, details, relations, stats))

    _, shifted_details, _, _ = DocumentationExtractor().parse(
        "docs/components.md", "\n\n" + source
    )
    shifted_table = next(record for record in shifted_details if record["kind"] == "md_table")
    assert shifted_table["record_id"] == table["record_id"]
    assert shifted_table["line_start"] == table["line_start"] + 2
    assert shifted_table["content_hash"] != table["content_hash"]


def test_embedded_mermaid_uses_diagram_extractor_and_document_line_coordinates() -> None:
    source = (
        "# Flow\n"
        "\n"
        "```mermaid\n"
        "flowchart TD\n"
        "A[Start] --> B[Done]\n"
        "```\n"
    )
    index, details, relations, stats = DocumentationExtractor().parse("docs/flow.md", source)
    embedded_file = next(record for record in details if record["kind"] == "mermaid_file")
    nodes = [record for record in details if record["kind"] == "mermaid_node"]
    edge = next(record for record in relations if record["relation"] == "diagram_edge")

    assert embedded_file["file"] == "docs/flow.md"
    assert embedded_file["document_id"] == index[0]["id"]
    assert embedded_file["embedded"] is True
    assert (embedded_file["line_start"], embedded_file["line_end"]) == (4, 5)
    assert {node["name"] for node in nodes} == {"A", "B"}
    assert all(node["line_start"] == 5 and node["document_id"] == index[0]["id"] for node in nodes)
    assert edge["line_start"] == 5
    assert edge["resolution_status"] == "resolved"
    assert any(relation["relation"] == "contains_embedded_diagram" for relation in relations)
    assert stats["embedded_diagram_count"] == 1


def test_planning_todo_json_emits_tasks_decisions_and_acceptance_without_secret_values() -> None:
    source = json.dumps(
        {
            "track": "contextual-editor",
            "goal": "Build contextual records",
            "core_decisions": ["Use the existing hub task queue."],
            "tasks": [
                {
                    "id": "CTX-001",
                    "title": "Extract records",
                    "status": "todo",
                    "depends_on": [],
                    "acceptance_criteria": ["token: never-copy-this"],
                }
            ],
        },
        indent=2,
    )
    index, details, relations, stats = JsonDocumentExtractor().parse(
        "todos/todo.contextual-editor.json", source
    )
    task = next(record for record in details if record["kind"] == "todo_task")
    decision = next(record for record in details if record["kind"] == "todo_decision")
    acceptance = next(record for record in details if record["kind"] == "todo_acceptance")

    assert index[0]["kind"] == "todo_file"
    assert task["task_id"] == "CTX-001"
    assert task["record_kind"] == "todo_task"
    assert task["line_start"] < task["line_end"]
    assert decision["decision_group"] == "core_decisions"
    assert acceptance["name"] == "token: [REDACTED]"
    assert "never-copy-this" not in json.dumps((index, details, relations, stats))
    assert {relation["relation"] for relation in relations} == {
        "contains_task",
        "contains_decision",
        "has_acceptance_criterion",
    }
    assert stats["task_count"] == stats["decision_count"] == stats["acceptance_count"] == 1


def test_json_schema_emits_pointer_id_and_ref_records_with_normalized_ranges() -> None:
    source = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:order",
            "$defs": {"Money": {"type": "number"}},
            "properties": {"total": {"$ref": "#/$defs/Money"}},
        },
        indent=2,
    )
    index, details, relations, stats = JsonDocumentExtractor().parse(
        "schemas/order.schema.json", source
    )

    kinds = {record["kind"] for record in details}
    assert {"json_schema_pointer", "json_schema_id", "json_schema_ref"} <= kinds
    schema_id = next(record for record in details if record["kind"] == "json_schema_id")
    schema_ref = next(record for record in details if record["kind"] == "json_schema_ref")
    assert schema_id["schema_id"] == "urn:order"
    assert schema_ref["ref"] == "#/$defs/Money"
    assert all(
        record["record_kind"] == record["kind"]
        and record["line_start"] <= record["line_end"]
        and len(record["content_hash"]) == 64
        for record in index + details + relations
    )
    assert stats["pointer_count"] >= 3
    assert stats["id_record_count"] == 1


def test_python_test_cases_and_fixture_relations_are_derived_from_ast_only() -> None:
    source = (
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def client():\n"
        "    return object()\n"
        "\n"
        "@pytest.mark.parametrize('value', [1])\n"
        "def test_request(client, tmp_path, value):\n"
        "    assert client\n"
    )
    _, details, relations, _ = TextFileExtractor().parse("tests/test_api.py", source)
    fixture = next(record for record in details if record.get("record_kind") == "fixture")
    test_case = next(record for record in details if record.get("record_kind") == "test_case")
    fixture_relations = [
        record for record in relations if record.get("record_kind") == "fixture_relation"
    ]

    assert fixture["name"] == "client"
    assert (test_case["line_start"], test_case["line_end"]) == (8, 9)
    assert {record["target"] for record in fixture_relations} == {"client", "tmp_path"}
    assert next(record for record in fixture_relations if record["target"] == "client")[
        "resolution_status"
    ] == "resolved"
    assert next(record for record in fixture_relations if record["target"] == "tmp_path")[
        "resolution_status"
    ] == "unresolved"
    assert all(record["source_kind"] == "python_function" for record in fixture_relations)
    assert all(record["target"] != "value" for record in fixture_relations)


def test_typescript_test_cases_and_destructured_fixtures_are_structural_records() -> None:
    source = (
        "import { test, expect } from '@playwright/test';\n"
        "\n"
        "test('opens editor', async ({ page, request: api }) => {\n"
        "  await page.goto('/editor');\n"
        "  await expect(page).toHaveTitle(/Editor/);\n"
        "});\n"
    )
    _, details, relations, stats = TextFileExtractor().parse(
        "frontend/editor.spec.ts", source
    )
    test_case = next(record for record in details if record.get("record_kind") == "test_case")
    fixtures = [
        record for record in relations if record.get("record_kind") == "fixture_relation"
    ]

    assert test_case["kind"] == "typescript_call"
    assert test_case["name"] == "opens editor"
    assert (test_case["line_start"], test_case["line_end"]) == (3, 6)
    assert {record["target"] for record in fixtures} == {"page", "request"}
    assert all(record["source_kind"] == "typescript_call" for record in fixtures)
    assert stats["test_case_count"] == 1
