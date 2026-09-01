from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.spreadsheet_actual_diff_service import SpreadsheetActualDiffService
from ananta_contracts.spreadsheet_studio import SpreadsheetContractError, validate_formula
from ananta_contracts.spreadsheet_studio_v2 import WorkbookSnapshotV2, merge_execution_candidate
from tests.spreadsheet_studio.helpers import service
from worker.spreadsheet.formula_parser import parse_formula, render_formula


def _assert_schema(filename: str, value: dict) -> None:
    path = Path(__file__).parents[2] / "schemas" / "spreadsheet-studio" / filename
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def rich_snapshot() -> dict:
    formula = {
        "op": "add",
        "left": {"op": "cell", "sheet_id": "sheet-one", "cell": "A1"},
        "right": {"op": "literal", "value": 1},
    }
    return {
        "schema": "ananta.spreadsheet-workbook-snapshot.v2",
        "snapshot_id": "snapshot-rich",
        "document_version_id": "document-version-rich",
        "locale": "de-DE",
        "timezone": "Europe/Berlin",
        "date_system": "1900",
        "recalc_profile": "automatic",
        "engine": {"name": "libreoffice-calc", "version": "25.2"},
        "styles": [
            {
                "style_id": "style-base",
                "number_format": "0.00",
                "bold": False,
                "italic": False,
                "fill_color": None,
            }
        ],
        "named_ranges": [
            {
                "named_range_id": "range-budget",
                "name": "Budget",
                "sheet_id": "sheet-one",
                "start": "A1",
                "end": "C1",
            }
        ],
        "tables": [
            {
                "table_id": "table-budget",
                "name": "BudgetTable",
                "sheet_id": "sheet-one",
                "start": "A1",
                "end": "C1",
                "header_row": False,
            }
        ],
        "charts": [
            {
                "chart_id": "chart-budget",
                "kind": "bar",
                "sheet_id": "sheet-one",
                "anchor": "E1",
                "source_start": "A1",
                "source_end": "C1",
            }
        ],
        "dependencies": [
            {
                "from_sheet_id": "sheet-one",
                "from_cell": "A1",
                "to_sheet_id": "sheet-one",
                "to_cell": "C1",
                "kind": "cell",
            }
        ],
        "unsupported_objects": [],
        "sheets": [
            {
                "sheet_id": "sheet-one",
                "name": "Budget",
                "visibility": "visible",
                "cells": [
                    {
                        "row": 1,
                        "column": 1,
                        "address": "A1",
                        "raw_value": 1,
                        "displayed_value": "1,00",
                        "formula_text": None,
                        "formula_ast": None,
                        "style_id": "style-base",
                    },
                    {
                        "row": 1,
                        "column": 3,
                        "address": "C1",
                        "raw_value": 2,
                        "displayed_value": "2,00",
                        "formula_text": "=A1+1",
                        "formula_ast": formula,
                        "style_id": "style-base",
                    },
                ],
            }
        ],
    }


def _proposal(document: dict, *, proposal_id: str, actions: list[dict], validators: list[dict]) -> dict:
    return {
        "schema": "ananta.spreadsheet-proposal.v1",
        "proposal_id": proposal_id,
        "document_id": document["document_id"],
        "expected_version": document["version"],
        "base_snapshot_digest": document["snapshot_digest"],
        "actions": actions,
        "validators": validators,
        "automatic_promotion": True,
    }


def test_v2_contract_separates_coordinates_values_formula_and_environment() -> None:
    parsed = WorkbookSnapshotV2.from_mapping(rich_snapshot())
    assert parsed.execution_v1().sheets[0]["cells"][1]["formula"]["op"] == "add"
    assert parsed.to_dict()["sheets"][0]["cells"][1]["formula_text"] == "=A1+1"
    assert parsed.to_dict()["sheets"][0]["cells"][0]["displayed_value"] == "1,00"

    malformed = rich_snapshot()
    malformed["sheets"][0]["cells"][0]["row"] = 2
    with pytest.raises(SpreadsheetContractError, match="coordinate_binding_invalid"):
        WorkbookSnapshotV2.from_mapping(malformed)
    malformed = rich_snapshot()
    malformed["sheets"][0]["cells"][1]["formula_text"] = "WEBSERVICE('https://example.test')"
    with pytest.raises(SpreadsheetContractError, match="formula_text_invalid"):
        WorkbookSnapshotV2.from_mapping(malformed)


def test_rich_snapshot_promotes_and_exposes_paginated_actual_diff(tmp_path) -> None:
    studio = service(tmp_path / "rich.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Rich budget",
        snapshot=rich_snapshot(),
        document_id="rich-document",
    )
    result = studio.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=_proposal(
            document,
            proposal_id="rich-proposal",
            actions=[
                {
                    "action_id": "set-budget",
                    "kind": "set_value",
                    "sheet_id": "sheet-one",
                    "cell": "A1",
                    "value": 42,
                    "formula": None,
                }
            ],
            validators=[
                {
                    "validator_id": "budget-equals",
                    "kind": "equals",
                    "sheet_id": "sheet-one",
                    "cell": "A1",
                    "expected": 42,
                    "minimum": None,
                    "maximum": None,
                }
            ],
        ),
    )
    assert result["state"] == "promoted"
    assert result["candidate_snapshot"]["schema"].endswith(".v2")
    _assert_schema("workbook-snapshot.v2.json", result["candidate_snapshot"])
    _assert_schema("actual-diff.v1.json", result["actual_diff"])
    assert result["candidate_snapshot"]["locale"] == "de-DE"
    assert result["candidate_snapshot"]["named_ranges"][0]["name"] == "Budget"
    assert result["actual_diff"]["total"] >= 1
    cell = next(item for item in result["actual_diff"]["items"] if item["kind"] == "cell")
    assert cell["direct"] is True and cell["action_ids"] == ["set-budget"]
    page = studio.get_proposal_diff(
        tenant_id="tenant-a",
        proposal_id="rich-proposal",
        principal_id="owner-a",
        offset=0,
        limit=1,
    )
    assert len(page["items"]) == 1
    assert page["has_more"] is (page["total"] > 1)
    assert page["diff_digest"] == result["actual_diff"]["diff_digest"]


def test_rich_snapshot_exposes_loss_explicit_paginated_viewport(tmp_path) -> None:
    studio = service(tmp_path / "viewport.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Rich viewport",
        snapshot=rich_snapshot(),
        document_id="viewport-document",
    )
    first = studio.get_viewport(
        tenant_id="tenant-a",
        document_id=document["document_id"],
        principal_id="owner-a",
        sheet_id="sheet-one",
        start="A1",
        end="C1",
        limit=1,
    )
    second = studio.get_viewport(
        tenant_id="tenant-a",
        document_id=document["document_id"],
        principal_id="owner-a",
        sheet_id="sheet-one",
        start="A1",
        end="C1",
        offset=1,
        limit=1,
    )
    assert first["total"] == first["backend_cell_count"] == 2
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["snapshot_digest"] == second["snapshot_digest"] == document["snapshot_digest"]
    assert first["projection_digest"] != second["projection_digest"]
    _assert_schema("workbook-viewport.v1.json", first)
    with pytest.raises(ValueError, match="viewport_too_large"):
        studio.get_viewport(
            tenant_id="tenant-a",
            document_id=document["document_id"],
            principal_id="owner-a",
            sheet_id="sheet-one",
            start="A1",
            end="Z1000",
        )
    with pytest.raises(PermissionError, match="owner_required"):
        studio.get_viewport(
            tenant_id="tenant-a",
            document_id=document["document_id"],
            principal_id="owner-b",
            sheet_id="sheet-one",
            start="A1",
            end="C1",
        )


def test_historical_viewport_is_bound_to_the_requested_immutable_version(tmp_path) -> None:
    studio = service(tmp_path / "historical-viewport.sqlite3")
    original = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Versioned viewport",
        snapshot=rich_snapshot(),
        document_id="versioned-viewport",
    )
    promoted = studio.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=_proposal(
            original,
            proposal_id="viewport-promotion",
            actions=[
                {
                    "action_id": "change-a1",
                    "kind": "set_value",
                    "sheet_id": "sheet-one",
                    "cell": "A1",
                    "value": 99,
                    "formula": None,
                }
            ],
            validators=[],
        ),
    )
    assert promoted["state"] == "promoted"

    historical = studio.get_version_viewport(
        tenant_id="tenant-a",
        document_id="versioned-viewport",
        version=1,
        principal_id="owner-a",
        sheet_id="sheet-one",
        start="A1",
        end="A1",
    )
    current = studio.get_viewport(
        tenant_id="tenant-a",
        document_id="versioned-viewport",
        principal_id="owner-a",
        sheet_id="sheet-one",
        start="A1",
        end="A1",
    )

    assert historical["snapshot_digest"] == original["snapshot_digest"]
    assert historical["cells"][0]["displayed_value"] == "1,00"
    assert current["snapshot_digest"] != historical["snapshot_digest"]
    assert current["cells"][0]["raw_value"] == 99


def test_structural_actions_rebase_rich_ranges_tables_and_charts() -> None:
    base = WorkbookSnapshotV2.from_mapping(rich_snapshot())
    execution = base.execution_v1().to_dict()
    for sheet in execution["sheets"]:
        for cell in sheet["cells"]:
            cell["address"] = cell["address"][0] + str(int(cell["address"][1:]) + 1)
            if cell["formula"] is not None:
                cell["formula"]["left"]["cell"] = "A2"
    candidate = merge_execution_candidate(
        base=base.to_dict(),
        candidate=execution,
        actions=[
            {
                "action_id": "insert-row",
                "kind": "insert_rows",
                "sheet_id": "sheet-one",
                "start_row": 1,
                "count": 1,
            }
        ],
        engine_name="deterministic-mock",
        engine_version="v1",
    ).to_dict()
    assert candidate["named_ranges"][0]["start"] == "A2"
    assert candidate["tables"][0]["end"] == "C2"
    assert candidate["charts"][0]["anchor"] == "E2"
    assert candidate["dependencies"][0]["to_cell"] == "C2"
    diff = SpreadsheetActualDiffService().build(
        before=base.to_dict(),
        after=candidate,
        actions=[
            {
                "action_id": "insert-row",
                "kind": "insert_rows",
                "sheet_id": "sheet-one",
                "start_row": 1,
                "count": 1,
            }
        ],
    )
    assert next(item for item in diff["items"] if item["kind"] == "structure")["direct"] is True


def test_extended_formula_ast_parser_and_renderer_remain_closed() -> None:
    mapping = {"budget": "sheet-one", "profit-loss": "sheet-one"}
    formula = parse_formula(
        "=IF(A1>=10,AVERAGE(A1:A3),-1)",
        current_sheet_id="sheet-one",
        sheet_ids_by_name=mapping,
    )
    assert validate_formula(formula)["op"] == "if"
    rendered = render_formula(formula, {"sheet-one": "Budget"})
    assert rendered.startswith("IF(") and "AVERAGE" in rendered and ">=" in rendered
    quoted_sheet = parse_formula(
        "='Profit-Loss'!A1*-1",
        current_sheet_id="sheet-one",
        sheet_ids_by_name=mapping,
    )
    assert quoted_sheet["left"]["op"] == "cell"
    assert quoted_sheet["right"]["op"] == "negate"
    with pytest.raises(SpreadsheetContractError, match="formula_range_order_invalid"):
        validate_formula({"op": "sum_range", "sheet_id": "sheet-one", "start": "B1", "end": "A2"})
    with pytest.raises(SpreadsheetContractError, match="formula_op_invalid"):
        validate_formula({"op": "webservice", "url": "https://example.test"})


def test_rich_unsupported_objects_are_capability_gated(tmp_path) -> None:
    studio = service(tmp_path / "unsupported.sqlite3")
    value = rich_snapshot()
    value["unsupported_objects"] = [
        {"object_id": "macro-one", "kind": "macro", "reason_code": "spreadsheet_macro_forbidden"}
    ]
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Unsupported",
        snapshot=value,
        document_id="unsupported-document",
    )
    with pytest.raises(PermissionError, match="unsupported_semantics"):
        studio.execute_proposal(
            tenant_id="tenant-a",
            principal_id="owner-a",
            proposal=_proposal(
                document,
                proposal_id="unsupported-proposal",
                actions=[
                    {
                        "action_id": "set",
                        "kind": "set_value",
                        "sheet_id": "sheet-one",
                        "cell": "A1",
                        "value": 2,
                        "formula": None,
                    }
                ],
                validators=[],
            ),
        )


def test_actual_diff_reports_visibility_and_object_updates() -> None:
    before = rich_snapshot()
    after = copy.deepcopy(before)
    after["sheets"][0]["visibility"] = "hidden"
    after["named_ranges"][0]["end"] = "B1"
    diff = SpreadsheetActualDiffService().build(before=before, after=after, limit=100)
    kinds = {item["kind"] for item in diff["items"]}
    assert {"sheet_visibility", "named_range"} <= kinds
