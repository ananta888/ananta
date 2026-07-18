from __future__ import annotations

import json

import pytest
from rag_helper.extractors.tabular_notebook_extractor import (
    DelimitedTextExtractor,
    NotebookExtractor,
)


@pytest.mark.parametrize(
    ("rel_path", "source"),
    [
        ("data/users.csv", "id,active,created,name\n1,true,2026-01-02,Ada\n2,false,2026-02-03,Lin\n"),
        ("data/users.tsv", "id\tactive\tcreated\tname\n1\ttrue\t2026-01-02\tAda\n2\tfalse\t2026-02-03\tLin\n"),
    ],
)
def test_delimited_text_indexes_headers_and_inferred_schema_without_values(rel_path: str, source: str) -> None:
    index, details, relations, stats = DelimitedTextExtractor().parse(rel_path, source)
    columns = {item["name"]: item for item in details if item.get("kind", "").endswith("_column")}
    assert columns["id"]["inferred_type"] == "integer"
    assert columns["active"]["inferred_type"] == "boolean"
    assert columns["created"]["inferred_type"] == "date"
    assert columns["name"]["inferred_type"] == "string"
    assert index[0]["summary"]["values_included"] is False
    assert relations == []
    assert stats["sampled_row_count"] == 2
    serialized = json.dumps((index, details, stats))
    assert "Ada" not in serialized and "Lin" not in serialized


def test_delimited_text_enforces_row_column_and_cell_limits_with_diagnostics() -> None:
    extractor = DelimitedTextExtractor(max_rows=1, sample_rows=1, max_columns=2, max_cell_chars=4)
    _, details, _, stats = extractor.parse("data/limited.csv", "a,b\n12345,2\n3,4\n")
    codes = {item.get("code") for item in details if item.get("kind") == "diagnostic"}
    assert "tabular_cell_limit_exceeded" in codes
    assert "tabular_row_limit_reached" in codes
    assert stats["diagnostic_count"] == 2

    _, details, _, stats = extractor.parse("data/wide.csv", "a,b,c\n1,2,3\n")
    assert any(item.get("code") == "tabular_column_limit_exceeded" for item in details)
    assert stats["diagnostic_count"] == 1


def test_notebook_extracts_markdown_and_code_cells_but_excludes_outputs_and_attachments() -> None:
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Analysis\n", "Documentation"],
                "attachments": {"image.png": {"image/png": "BINARY_ATTACHMENT_MUST_NOT_LEAK"}},
            },
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": 42,
                "source": ["class Report:\n", "    pass\n", "def build():\n", "    return Report()\n"],
                "outputs": [
                    {"output_type": "display_data", "data": {"image/png": "BINARY_OUTPUT_MUST_NOT_LEAK"}},
                    {"output_type": "stream", "name": "stdout", "text": "SECRET_OUTPUT_MUST_NOT_LEAK"},
                ],
            },
        ],
    }
    index, details, relations, stats = NotebookExtractor().parse(
        "notebooks/analysis.ipynb", json.dumps(notebook, indent=2)
    )
    markdown = next(item for item in details if item["kind"] == "ipynb_markdown_cell")
    code = next(item for item in details if item["kind"] == "ipynb_code_cell")
    serialized = json.dumps((index, details, relations, stats))
    assert markdown["headings"] == ["Analysis"]
    assert {item["name"] for item in code["symbols"]} == {"Report", "build"}
    assert code["executed"] is False
    assert code["outputs_included"] is False
    assert markdown["attachments_included"] is False
    assert "BINARY_ATTACHMENT_MUST_NOT_LEAK" not in serialized
    assert "BINARY_OUTPUT_MUST_NOT_LEAK" not in serialized
    assert "SECRET_OUTPUT_MUST_NOT_LEAK" not in serialized
    assert stats["cell_count"] == 2
    assert len(relations) == 2


def test_notebook_invalid_json_and_cell_limits_have_honest_fallbacks() -> None:
    index, details, _, stats = NotebookExtractor().parse("notebooks/broken.ipynb", "{broken")
    assert index[0]["parser_mode"] == "text_index"
    assert details[0]["code"] == "notebook_json_parse_error"
    assert stats["diagnostic_count"] == 1

    notebook = {"cells": [{"cell_type": "raw", "source": "a"}, {"cell_type": "raw", "source": "b"}], "metadata": {}}
    _, details, _, stats = NotebookExtractor(max_cells=1).parse("notebooks/limited.ipynb", json.dumps(notebook))
    assert any(item.get("code") == "notebook_cell_limit_reached" for item in details)
    assert stats["cell_count"] == 1
