"""Deterministic evaluators for the closed Spreadsheet Studio validator union."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.spreadsheet_validation_reference_port import SpreadsheetValidationReferenceRepositoryPort
from ananta_contracts.spreadsheet_studio import canonical_digest, cell_coordinates
from ananta_contracts.spreadsheet_studio_v2 import execution_snapshot


class SpreadsheetValidationRuleEvaluator:
    def __init__(
        self,
        reference_repository: SpreadsheetValidationReferenceRepositoryPort | None = None,
    ) -> None:
        self._references = reference_repository

    def evaluate(
        self,
        *,
        snapshot: Mapping[str, Any],
        validators: Sequence[Mapping[str, Any]],
        tenant_id: str | None,
        actual_diff: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cells = _cells(snapshot)
        return [
            self._evaluate_one(validator, cells=cells, tenant_id=tenant_id, actual_diff=actual_diff)
            for validator in validators
        ]

    def _evaluate_one(
        self,
        validator: Mapping[str, Any],
        *,
        cells: Mapping[tuple[str, str], Mapping[str, Any]],
        tenant_id: str | None,
        actual_diff: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        kind = str(validator["kind"])
        observed: Any = None
        passed = False
        state = "incorrect"
        reason_code = "spreadsheet_validator_failed"
        reference_digest = None
        if kind in {"equals", "number_range", "formula_present", "cell_empty", "number_tolerance", "formula_pattern"}:
            cell = cells.get((str(validator["sheet_id"]), str(validator["cell"])))
            observed = cell
            passed = _evaluate_cell_rule(kind, validator, cell)
        elif kind in {"invariant", "sum_range", "range_rule"}:
            values = _range_cells(cells, validator["sheet_id"], validator["start"], validator["end"])
            observed = values
            passed = _evaluate_range_rule(kind, validator, values)
        elif kind == "change_scope":
            if actual_diff is None or int(actual_diff.get("total", -1)) != len(actual_diff.get("items", [])):
                state = "not_verifiable"
                reason_code = "spreadsheet_validation_diff_incomplete"
                observed = None
                passed = False
            else:
                observed = _changes_in_range(actual_diff, validator)
                passed = bool(observed) is (validator["expectation"] == "changed")
                reason_code = "spreadsheet_validator_unexpected_change"
        else:
            passed, observed, reference_digest, state, reason_code = self._evaluate_reference(
                validator, cells=cells, tenant_id=tenant_id
            )
        if state != "not_verifiable":
            state = "correct" if passed else "incorrect"
            reason_code = None if passed else reason_code
        return {
            "validator_id": str(validator["validator_id"]),
            "kind": kind,
            "passed": passed,
            "state": state,
            "reason_code": reason_code,
            "observed_digest": canonical_digest(observed),
            "reference_digest": reference_digest,
        }

    def _evaluate_reference(
        self,
        validator: Mapping[str, Any],
        *,
        cells: Mapping[tuple[str, str], Mapping[str, Any]],
        tenant_id: str | None,
    ) -> tuple[bool, Any, str | None, str, str | None]:
        if self._references is None or tenant_id is None:
            return False, None, None, "not_verifiable", "spreadsheet_validation_reference_unavailable"
        try:
            reference = self._references.get_reference(tenant_id, str(validator["reference_id"]))
        except KeyError:
            return False, None, None, "not_verifiable", "spreadsheet_validation_reference_not_found"
        reference_cells = _cells(reference["snapshot"])
        expected = _range_cells(
            reference_cells,
            validator["reference_sheet_id"],
            validator["reference_start"],
            validator["reference_end"],
        )
        observed = _range_cells(cells, validator["sheet_id"], validator["start"], validator["end"])
        passed = len(expected) == len(observed) and all(
            _reference_cell_equal(left, right, validator) for left, right in zip(expected, observed, strict=True)
        )
        return (
            passed,
            observed,
            str(reference["reference_digest"]),
            "correct" if passed else "incorrect",
            (None if passed else "spreadsheet_validator_failed"),
        )


def _cells(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    parsed = execution_snapshot(snapshot)
    return {
        (str(sheet["sheet_id"]), str(cell["address"])): dict(cell) for sheet in parsed.sheets for cell in sheet["cells"]
    }


def _range_cells(
    cells: Mapping[tuple[str, str], Mapping[str, Any]], sheet_id: object, start: object, end: object
) -> list[dict[str, Any] | None]:
    start_row, start_column = cell_coordinates(start)
    end_row, end_column = cell_coordinates(end)
    sheet = str(sheet_id)
    return [
        copy.deepcopy(cells.get((sheet, _cell_address(row, column))))
        for row in range(start_row, end_row + 1)
        for column in range(start_column, end_column + 1)
    ]


def _evaluate_cell_rule(kind: str, validator: Mapping[str, Any], cell: Mapping[str, Any] | None) -> bool:
    if kind == "equals":
        return cell is not None and cell["value"] == validator["expected"]
    if kind == "number_range":
        value = cell.get("value") if cell else None
        return _number(value) and validator["minimum"] <= float(value) <= validator["maximum"]
    if kind == "formula_present":
        return cell is not None and cell["formula"] is not None
    if kind == "cell_empty":
        return cell is None or (cell["value"] is None and cell["formula"] is None)
    if kind == "number_tolerance":
        value = cell.get("value") if cell else None
        return _number(value) and _within_tolerance(float(value), float(validator["expected"]), validator)
    formula = cell.get("formula") if cell else None
    if formula is None:
        return False
    if not validator["allow_relative_references"]:
        return formula == validator["expected_formula"]
    return _relative_formula(formula, str(validator["cell"])) == _relative_formula(
        validator["expected_formula"], str(validator["expected_origin"])
    )


def _evaluate_range_rule(kind: str, validator: Mapping[str, Any], cells: Sequence[Mapping[str, Any] | None]) -> bool:
    values = [cell.get("value") if cell else None for cell in cells]
    if kind == "sum_range":
        return all(_number(value) for value in values) and _within_tolerance(
            sum(float(value) for value in values), float(validator["expected"]), validator
        )
    if kind == "invariant":
        rule = validator["rule"]
        if rule == "non_empty":
            return all(value is not None for value in values)
        if rule == "non_negative":
            return all(_number(value) and float(value) >= 0 for value in values)
        if rule == "unique":
            populated = [value for value in values if value is not None]
            return len(populated) == len({canonical_digest(value) for value in populated})
        return all(not (isinstance(value, str) and value.startswith("#")) for value in values)
    expected_type = validator["value_type"]
    for value in values:
        if value is None:
            if validator["allow_empty"]:
                continue
            return False
        if expected_type == "number" and not _number(value):
            return False
        if expected_type == "string" and not isinstance(value, str):
            return False
        if expected_type == "boolean" and not isinstance(value, bool):
            return False
        if validator["minimum"] is not None and (not _number(value) or float(value) < validator["minimum"]):
            return False
        if validator["maximum"] is not None and (not _number(value) or float(value) > validator["maximum"]):
            return False
    return True


def _changes_in_range(actual_diff: Mapping[str, Any] | None, validator: Mapping[str, Any]) -> list[str]:
    if actual_diff is None:
        return []
    start_row, start_column = cell_coordinates(validator["start"])
    end_row, end_column = cell_coordinates(validator["end"])
    result = []
    for item in actual_diff.get("items", []):
        if item.get("sheet_id") != validator["sheet_id"]:
            continue
        cell = item.get("cell")
        if cell is None:
            if item.get("kind") == "structure":
                result.append(str(item["object_id"]))
            continue
        row, column = cell_coordinates(cell)
        if start_row <= row <= end_row and start_column <= column <= end_column:
            result.append(str(item["object_id"]))
    return sorted(result)


def _reference_cell_equal(
    expected: Mapping[str, Any] | None, actual: Mapping[str, Any] | None, validator: Mapping[str, Any]
) -> bool:
    if expected is None or actual is None:
        return expected is actual
    left, right = expected.get("value"), actual.get("value")
    values_equal = (
        _within_tolerance(float(right), float(left), validator) if _number(left) and _number(right) else left == right
    )
    return values_equal and (not validator["compare_formulas"] or expected.get("formula") == actual.get("formula"))


def _within_tolerance(actual: float, expected: float, validator: Mapping[str, Any]) -> bool:
    digits = int(validator.get("rounding_digits", 15))
    difference = abs(round(actual, digits) - round(expected, digits))
    allowed = max(float(validator["absolute_tolerance"]), abs(expected) * float(validator["relative_tolerance"]))
    return difference <= allowed


def _relative_formula(value: Mapping[str, Any], origin: str) -> dict[str, Any]:
    origin_row, origin_column = cell_coordinates(origin)
    result = copy.deepcopy(dict(value))

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("op") == "cell":
            row, column = cell_coordinates(node.pop("cell"))
            node["offset"] = [row - origin_row, column - origin_column]
        elif node.get("op") in {"sum_range", "average_range", "min_range", "max_range"}:
            start_row, start_column = cell_coordinates(node.pop("start"))
            end_row, end_column = cell_coordinates(node.pop("end"))
            node["start_offset"] = [start_row - origin_row, start_column - origin_column]
            node["end_offset"] = [end_row - origin_row, end_column - origin_column]
        for field in ("left", "right", "expression", "condition", "then", "else"):
            visit(node.get(field))

    visit(result)
    return result


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _cell_address(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


__all__ = ["SpreadsheetValidationRuleEvaluator"]
