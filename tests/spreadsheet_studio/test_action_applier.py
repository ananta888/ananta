from __future__ import annotations

from openpyxl import Workbook

from ananta_contracts.spreadsheet_studio import validate_action
from worker.spreadsheet.action_applier import SpreadsheetActionApplier


def _workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet 1"
    sheet["A1"] = 1
    sheet["B1"] = 2
    return workbook


def test_applier_handles_copy_clear_and_bounded_format_ranges() -> None:
    workbook = _workbook()
    actions = tuple(
        validate_action(action)
        for action in (
            {
                "action_id": "copy",
                "kind": "copy_range",
                "source_sheet_id": "sheet-one",
                "source_start": "A1",
                "source_end": "B1",
                "target_sheet_id": "sheet-one",
                "target_start": "A2",
            },
            {
                "action_id": "format",
                "kind": "format_range",
                "sheet_id": "sheet-one",
                "start": "A2",
                "end": "B2",
                "style": {"number_format": "0.00", "bold": True, "italic": None, "fill_color": "FFCC00"},
            },
            {
                "action_id": "clear",
                "kind": "clear_range",
                "sheet_id": "sheet-one",
                "start": "B1",
                "end": "B1",
            },
        )
    )
    styles = {("sheet-one", "A1"): None, ("sheet-one", "B1"): None}

    direct = SpreadsheetActionApplier().apply(
        workbook=workbook,
        sheet_names={"sheet-one": "Sheet 1"},
        actions=actions,
        formula_asts={},
        style_refs=styles,
    )

    sheet = workbook["Sheet 1"]
    assert (sheet["A2"].value, sheet["B2"].value, sheet["B1"].value) == (1, 2, None)
    assert sheet["A2"].font.bold is True
    assert sheet["A2"].number_format == "0.00"
    assert direct[("sheet-one", "A2")] == ["copy", "format"]
    assert direct[("sheet-one", "B1")] == ["clear"]


def test_applier_rebases_metadata_for_limited_row_and_column_operations() -> None:
    workbook = _workbook()
    formulas = {("sheet-one", "A1"): {"op": "literal", "value": 1}}
    styles = {("sheet-one", "A1"): "style-one", ("sheet-one", "B1"): "style-two"}
    actions = tuple(
        validate_action(action)
        for action in (
            {"action_id": "row", "kind": "insert_rows", "sheet_id": "sheet-one", "start_row": 1, "count": 1},
            {"action_id": "column", "kind": "delete_columns", "sheet_id": "sheet-one", "start_column": 2, "count": 1},
        )
    )

    SpreadsheetActionApplier().apply(
        workbook=workbook,
        sheet_names={"sheet-one": "Sheet 1"},
        actions=actions,
        formula_asts=formulas,
        style_refs=styles,
    )

    sheet = workbook["Sheet 1"]
    assert sheet["A2"].value == 1
    assert sheet["B2"].value is None
    assert formulas == {("sheet-one", "A2"): {"op": "literal", "value": 1}}
    assert styles == {("sheet-one", "A2"): "style-one"}
