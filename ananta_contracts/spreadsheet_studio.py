"""Closed dependency-free contracts for governed spreadsheet transformations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
ACTION_KINDS = frozenset(
    {
        "set_value",
        "set_formula",
        "clear_cell",
        "clear_range",
        "copy_range",
        "format_range",
        "insert_rows",
        "delete_rows",
        "insert_columns",
        "delete_columns",
    }
)
FORMULA_OPS = frozenset(
    {
        "literal",
        "cell",
        "add",
        "subtract",
        "multiply",
        "divide",
        "equal",
        "not_equal",
        "less_than",
        "less_equal",
        "greater_than",
        "greater_equal",
        "negate",
        "if",
        "sum_range",
        "average_range",
        "min_range",
        "max_range",
    }
)
VALIDATOR_KINDS = frozenset(
    {
        "equals",
        "number_range",
        "formula_present",
        "cell_empty",
        "number_tolerance",
        "formula_pattern",
        "invariant",
        "sum_range",
        "range_rule",
        "reference_range",
        "change_scope",
    }
)


class SpreadsheetContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def require_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return text


def require_digest(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST.fullmatch(text):
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return text


def require_cell(value: object, field: str = "cell") -> str:
    text = str(value or "").strip().upper()
    if not _CELL.fullmatch(text):
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return text


def cell_coordinates(value: object, field: str = "cell") -> tuple[int, int]:
    """Return one-based row/column coordinates for a validated A1 address."""

    cell = require_cell(value, field)
    letters, digits = re.fullmatch(r"([A-Z]+)([0-9]+)", cell).groups()  # type: ignore[union-attr]
    column = 0
    for character in letters:
        column = column * 26 + ord(character) - ord("A") + 1
    return int(digits), column


def _positive_int(value: object, field: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return value


def _range(start: object, end: object, *, prefix: str) -> tuple[str, str]:
    normalized_start = require_cell(start, f"{prefix}_start")
    normalized_end = require_cell(end, f"{prefix}_end")
    start_row, start_column = cell_coordinates(normalized_start)
    end_row, end_column = cell_coordinates(normalized_end)
    if start_row > end_row or start_column > end_column:
        raise SpreadsheetContractError(f"spreadsheet_{prefix}_order_invalid")
    if (end_row - start_row + 1) * (end_column - start_column + 1) > 10_000:
        raise SpreadsheetContractError(f"spreadsheet_{prefix}_too_large")
    return normalized_start, normalized_end


def _exact(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise SpreadsheetContractError(f"spreadsheet_{name}_fields_invalid")


def _json_scalar(value: object, field: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value.encode()) > 16_384:
            raise SpreadsheetContractError(f"spreadsheet_{field}_too_large")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return value
    raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")


def validate_json_scalar(value: object, field: str) -> str | int | float | bool | None:
    """Public bounded JSON-scalar primitive shared by additive contracts."""

    return _json_scalar(value, field)


def validate_formula(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or depth > 8:
        raise SpreadsheetContractError("spreadsheet_formula_invalid")
    counter = nodes if nodes is not None else [0]
    counter[0] += 1
    if counter[0] > 64:
        raise SpreadsheetContractError("spreadsheet_formula_too_complex")
    op = str(value.get("op") or "").strip()
    if op not in FORMULA_OPS:
        raise SpreadsheetContractError("spreadsheet_formula_op_invalid")
    if op == "literal":
        _exact(value, {"op", "value"}, "formula_literal")
        return {"op": op, "value": _json_scalar(value.get("value"), "formula_literal")}
    if op == "cell":
        _exact(value, {"op", "sheet_id", "cell"}, "formula_cell")
        return {
            "op": op,
            "sheet_id": require_id(value.get("sheet_id"), "formula_sheet_id"),
            "cell": require_cell(value.get("cell"), "formula_cell"),
        }
    if op in {"sum_range", "average_range", "min_range", "max_range"}:
        _exact(value, {"op", "sheet_id", "start", "end"}, "formula_range")
        start = require_cell(value.get("start"), "formula_start")
        end = require_cell(value.get("end"), "formula_end")
        start_row, start_column = cell_coordinates(start)
        end_row, end_column = cell_coordinates(end)
        if (
            start_row > end_row
            or start_column > end_column
            or (end_row - start_row + 1) * (end_column - start_column + 1) > 10_000
        ):
            raise SpreadsheetContractError("spreadsheet_formula_range_order_invalid")
        return {
            "op": op,
            "sheet_id": require_id(value.get("sheet_id"), "formula_sheet_id"),
            "start": start,
            "end": end,
        }
    if op == "negate":
        _exact(value, {"op", "expression"}, "formula_unary")
        return {
            "op": op,
            "expression": validate_formula(value.get("expression"), depth=depth + 1, nodes=counter),
        }
    if op == "if":
        _exact(value, {"op", "condition", "then", "else"}, "formula_if")
        return {
            "op": op,
            "condition": validate_formula(value.get("condition"), depth=depth + 1, nodes=counter),
            "then": validate_formula(value.get("then"), depth=depth + 1, nodes=counter),
            "else": validate_formula(value.get("else"), depth=depth + 1, nodes=counter),
        }
    _exact(value, {"op", "left", "right"}, "formula_binary")
    return {
        "op": op,
        "left": validate_formula(value.get("left"), depth=depth + 1, nodes=counter),
        "right": validate_formula(value.get("right"), depth=depth + 1, nodes=counter),
    }


@dataclass(frozen=True, slots=True)
class WorkbookSnapshotV1:
    SCHEMA: ClassVar[str] = "ananta.spreadsheet-workbook-snapshot.v1"
    schema: str
    snapshot_id: str
    document_version_id: str
    sheets: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> WorkbookSnapshotV1:
        _exact(value, {"schema", "snapshot_id", "document_version_id", "sheets"}, "snapshot")
        raw_sheets = value.get("sheets")
        if (
            value.get("schema") != cls.SCHEMA
            or not isinstance(raw_sheets, Sequence)
            or isinstance(raw_sheets, (str, bytes))
        ):
            raise SpreadsheetContractError("spreadsheet_snapshot_invalid")
        if not 1 <= len(raw_sheets) <= 64:
            raise SpreadsheetContractError("spreadsheet_sheet_count_invalid")
        sheets: list[dict[str, Any]] = []
        sheet_ids: set[str] = set()
        total_cells = 0
        for raw in raw_sheets:
            if not isinstance(raw, Mapping):
                raise SpreadsheetContractError("spreadsheet_sheet_invalid")
            _exact(raw, {"sheet_id", "name", "hidden", "cells"}, "sheet")
            sheet_id = require_id(raw.get("sheet_id"), "sheet_id")
            name = str(raw.get("name") or "").strip()
            cells = raw.get("cells")
            if sheet_id in sheet_ids or not 1 <= len(name) <= 128 or not isinstance(raw.get("hidden"), bool):
                raise SpreadsheetContractError("spreadsheet_sheet_invalid")
            if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
                raise SpreadsheetContractError("spreadsheet_cells_invalid")
            normalized_cells: list[dict[str, Any]] = []
            addresses: set[str] = set()
            for cell in cells:
                if not isinstance(cell, Mapping):
                    raise SpreadsheetContractError("spreadsheet_cell_invalid")
                _exact(cell, {"address", "value", "formula", "style_ref"}, "cell")
                address = require_cell(cell.get("address"))
                if address in addresses:
                    raise SpreadsheetContractError("spreadsheet_cell_duplicate")
                formula = cell.get("formula")
                normalized_cells.append(
                    {
                        "address": address,
                        "value": _json_scalar(cell.get("value"), "cell_value"),
                        "formula": validate_formula(formula) if formula is not None else None,
                        "style_ref": require_id(cell.get("style_ref"), "style_ref") if cell.get("style_ref") else None,
                    }
                )
                addresses.add(address)
            total_cells += len(normalized_cells)
            if total_cells > 100_000:
                raise SpreadsheetContractError("spreadsheet_cell_limit_exceeded")
            sheets.append({"sheet_id": sheet_id, "name": name, "hidden": raw["hidden"], "cells": normalized_cells})
            sheet_ids.add(sheet_id)
        return cls(
            cls.SCHEMA,
            require_id(value.get("snapshot_id"), "snapshot_id"),
            require_id(value.get("document_version_id"), "document_version_id"),
            tuple(sheets),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "document_version_id": self.document_version_id,
            "sheets": [dict(sheet) for sheet in self.sheets],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class SpreadsheetProposalV1:
    SCHEMA: ClassVar[str] = "ananta.spreadsheet-proposal.v1"
    schema: str
    proposal_id: str
    document_id: str
    expected_version: int
    base_snapshot_digest: str
    actions: tuple[Mapping[str, Any], ...]
    validators: tuple[Mapping[str, Any], ...]
    automatic_promotion: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SpreadsheetProposalV1:
        _exact(
            value,
            {
                "schema",
                "proposal_id",
                "document_id",
                "expected_version",
                "base_snapshot_digest",
                "actions",
                "validators",
                "automatic_promotion",
            },
            "proposal",
        )
        if value.get("schema") != cls.SCHEMA or not isinstance(value.get("automatic_promotion"), bool):
            raise SpreadsheetContractError("spreadsheet_proposal_invalid")
        version = value.get("expected_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SpreadsheetContractError("spreadsheet_expected_version_invalid")
        raw_actions = value.get("actions")
        raw_validators = value.get("validators")
        if (
            not isinstance(raw_actions, Sequence)
            or isinstance(raw_actions, (str, bytes))
            or not 1 <= len(raw_actions) <= 1_000
        ):
            raise SpreadsheetContractError("spreadsheet_actions_invalid")
        if (
            not isinstance(raw_validators, Sequence)
            or isinstance(raw_validators, (str, bytes))
            or len(raw_validators) > 100
        ):
            raise SpreadsheetContractError("spreadsheet_validators_invalid")
        return cls(
            cls.SCHEMA,
            require_id(value.get("proposal_id"), "proposal_id"),
            require_id(value.get("document_id"), "document_id"),
            version,
            require_digest(value.get("base_snapshot_digest"), "base_snapshot_digest"),
            tuple(_action(item) for item in raw_actions),
            tuple(_validator(item) for item in raw_validators),
            bool(value["automatic_promotion"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "actions": [dict(item) for item in self.actions],
            "validators": [dict(item) for item in self.validators],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def _action(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SpreadsheetContractError("spreadsheet_action_invalid")
    kind = str(value.get("kind") or "")
    if kind not in ACTION_KINDS:
        raise SpreadsheetContractError("spreadsheet_action_kind_invalid")
    action_id = require_id(value.get("action_id"), "action_id")
    if kind in {"set_value", "set_formula", "clear_cell"}:
        _exact(value, {"action_id", "kind", "sheet_id", "cell", "value", "formula"}, "action")
        raw_formula = value.get("formula")
        if (kind == "set_formula") != (raw_formula is not None):
            raise SpreadsheetContractError("spreadsheet_action_formula_invalid")
        if kind != "set_value" and value.get("value") is not None:
            raise SpreadsheetContractError("spreadsheet_action_value_invalid")
        return {
            "action_id": action_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "cell": require_cell(value.get("cell")),
            "value": _json_scalar(value.get("value"), "action_value"),
            "formula": validate_formula(raw_formula) if raw_formula is not None else None,
        }
    if kind == "clear_range":
        _exact(value, {"action_id", "kind", "sheet_id", "start", "end"}, "action_clear_range")
        start, end = _range(value.get("start"), value.get("end"), prefix="action_range")
        return {
            "action_id": action_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "start": start,
            "end": end,
        }
    if kind == "copy_range":
        _exact(
            value,
            {
                "action_id",
                "kind",
                "source_sheet_id",
                "source_start",
                "source_end",
                "target_sheet_id",
                "target_start",
            },
            "action_copy_range",
        )
        source_start, source_end = _range(
            value.get("source_start"), value.get("source_end"), prefix="action_source_range"
        )
        return {
            "action_id": action_id,
            "kind": kind,
            "source_sheet_id": require_id(value.get("source_sheet_id"), "source_sheet_id"),
            "source_start": source_start,
            "source_end": source_end,
            "target_sheet_id": require_id(value.get("target_sheet_id"), "target_sheet_id"),
            "target_start": require_cell(value.get("target_start"), "target_start"),
        }
    if kind == "format_range":
        _exact(value, {"action_id", "kind", "sheet_id", "start", "end", "style"}, "action_format_range")
        start, end = _range(value.get("start"), value.get("end"), prefix="action_range")
        style = value.get("style")
        if not isinstance(style, Mapping):
            raise SpreadsheetContractError("spreadsheet_action_style_invalid")
        _exact(style, {"number_format", "bold", "italic", "fill_color"}, "action_style")
        number_format = style.get("number_format")
        fill_color = style.get("fill_color")
        if number_format is not None and (not isinstance(number_format, str) or not 1 <= len(number_format) <= 128):
            raise SpreadsheetContractError("spreadsheet_action_number_format_invalid")
        if fill_color is not None and (
            not isinstance(fill_color, str) or not re.fullmatch(r"[0-9A-Fa-f]{6,8}", fill_color)
        ):
            raise SpreadsheetContractError("spreadsheet_action_fill_color_invalid")
        for field in ("bold", "italic"):
            if style.get(field) is not None and not isinstance(style.get(field), bool):
                raise SpreadsheetContractError(f"spreadsheet_action_{field}_invalid")
        if all(style.get(field) is None for field in style):
            raise SpreadsheetContractError("spreadsheet_action_style_empty")
        return {
            "action_id": action_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "start": start,
            "end": end,
            "style": {
                "number_format": number_format,
                "bold": style.get("bold"),
                "italic": style.get("italic"),
                "fill_color": fill_color.upper() if isinstance(fill_color, str) else None,
            },
        }
    if kind in {"insert_rows", "delete_rows"}:
        _exact(value, {"action_id", "kind", "sheet_id", "start_row", "count"}, "action_rows")
        return {
            "action_id": action_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "start_row": _positive_int(value.get("start_row"), "action_start_row", maximum=1_048_576),
            "count": _positive_int(value.get("count"), "action_row_count", maximum=1_000),
        }
    _exact(value, {"action_id", "kind", "sheet_id", "start_column", "count"}, "action_columns")
    return {
        "action_id": action_id,
        "kind": kind,
        "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
        "start_column": _positive_int(value.get("start_column"), "action_start_column", maximum=16_384),
        "count": _positive_int(value.get("count"), "action_column_count", maximum=1_000),
    }


def validate_action(value: object) -> dict[str, Any]:
    """Public closed-union validator for training and inference adapters."""

    return _action(value)


def _validator(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SpreadsheetContractError("spreadsheet_validator_invalid")
    kind = str(value.get("kind") or "")
    if kind not in VALIDATOR_KINDS:
        raise SpreadsheetContractError("spreadsheet_validator_kind_invalid")
    validator_id = require_id(value.get("validator_id"), "validator_id")
    if kind in {"equals", "number_range", "formula_present", "cell_empty"}:
        return _legacy_validator(value, validator_id=validator_id, kind=kind)
    if kind == "number_tolerance":
        _exact(
            value,
            {
                "validator_id",
                "kind",
                "sheet_id",
                "cell",
                "expected",
                "absolute_tolerance",
                "relative_tolerance",
                "rounding_digits",
            },
            "validator_number_tolerance",
        )
        return {
            "validator_id": validator_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "cell": require_cell(value.get("cell")),
            "expected": _required_number(value.get("expected"), "validator_expected"),
            **_tolerance(value),
        }
    if kind == "formula_pattern":
        _exact(
            value,
            {
                "validator_id",
                "kind",
                "sheet_id",
                "cell",
                "expected_formula",
                "expected_origin",
                "allow_relative_references",
            },
            "validator_formula_pattern",
        )
        if not isinstance(value.get("allow_relative_references"), bool):
            raise SpreadsheetContractError("spreadsheet_validator_formula_relative_invalid")
        return {
            "validator_id": validator_id,
            "kind": kind,
            "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
            "cell": require_cell(value.get("cell")),
            "expected_formula": validate_formula(value.get("expected_formula")),
            "expected_origin": require_cell(value.get("expected_origin"), "expected_origin"),
            "allow_relative_references": value["allow_relative_references"],
        }
    if kind in {"invariant", "sum_range", "range_rule", "change_scope"}:
        return _range_validator(value, validator_id=validator_id, kind=kind)
    return _reference_validator(value, validator_id=validator_id)


def validate_validator(value: object) -> dict[str, Any]:
    """Validate one member of the closed additive validator union."""

    return _validator(value)


def _legacy_validator(value: Mapping[str, Any], *, validator_id: str, kind: str) -> dict[str, Any]:
    _exact(value, {"validator_id", "kind", "sheet_id", "cell", "expected", "minimum", "maximum"}, "validator")
    minimum = value.get("minimum")
    maximum = value.get("maximum")
    if kind == "number_range" and (
        not _is_finite_number(minimum) or not _is_finite_number(maximum) or float(minimum) > float(maximum)
    ):
        raise SpreadsheetContractError("spreadsheet_validator_range_invalid")
    return {
        "validator_id": validator_id,
        "kind": kind,
        "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
        "cell": require_cell(value.get("cell")),
        "expected": _json_scalar(value.get("expected"), "validator_expected"),
        "minimum": float(minimum) if kind == "number_range" else None,
        "maximum": float(maximum) if kind == "number_range" else None,
    }


def _range_validator(value: Mapping[str, Any], *, validator_id: str, kind: str) -> dict[str, Any]:
    start, end = _range(value.get("start"), value.get("end"), prefix="validator_range")
    common = {
        "validator_id": validator_id,
        "kind": kind,
        "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
        "start": start,
        "end": end,
    }
    if kind == "invariant":
        _exact(value, {"validator_id", "kind", "sheet_id", "start", "end", "rule"}, "validator_invariant")
        if value.get("rule") not in {"non_empty", "non_negative", "unique", "no_errors"}:
            raise SpreadsheetContractError("spreadsheet_validator_invariant_rule_invalid")
        return {**common, "rule": value["rule"]}
    if kind == "sum_range":
        _exact(
            value,
            {
                "validator_id",
                "kind",
                "sheet_id",
                "start",
                "end",
                "expected",
                "absolute_tolerance",
                "relative_tolerance",
                "rounding_digits",
            },
            "validator_sum_range",
        )
        return {
            **common,
            "expected": _required_number(value.get("expected"), "validator_expected"),
            **_tolerance(value),
        }
    if kind == "range_rule":
        _exact(
            value,
            {
                "validator_id",
                "kind",
                "sheet_id",
                "start",
                "end",
                "value_type",
                "allow_empty",
                "minimum",
                "maximum",
            },
            "validator_range_rule",
        )
        minimum = value.get("minimum")
        maximum = value.get("maximum")
        if (
            value.get("value_type") not in {"any", "number", "string", "boolean"}
            or not isinstance(value.get("allow_empty"), bool)
            or (minimum is not None and not _is_finite_number(minimum))
            or (maximum is not None and not _is_finite_number(maximum))
            or (minimum is not None and maximum is not None and float(minimum) > float(maximum))
        ):
            raise SpreadsheetContractError("spreadsheet_validator_range_rule_invalid")
        return {
            **common,
            "value_type": value["value_type"],
            "allow_empty": value["allow_empty"],
            "minimum": float(minimum) if minimum is not None else None,
            "maximum": float(maximum) if maximum is not None else None,
        }
    _exact(
        value,
        {"validator_id", "kind", "sheet_id", "start", "end", "expectation"},
        "validator_change_scope",
    )
    if value.get("expectation") not in {"unchanged", "changed"}:
        raise SpreadsheetContractError("spreadsheet_validator_change_expectation_invalid")
    return {**common, "expectation": value["expectation"]}


def _reference_validator(value: Mapping[str, Any], *, validator_id: str) -> dict[str, Any]:
    _exact(
        value,
        {
            "validator_id",
            "kind",
            "reference_id",
            "reference_sheet_id",
            "reference_start",
            "reference_end",
            "sheet_id",
            "start",
            "end",
            "absolute_tolerance",
            "relative_tolerance",
            "compare_formulas",
        },
        "validator_reference_range",
    )
    reference_start, reference_end = _range(
        value.get("reference_start"), value.get("reference_end"), prefix="validator_reference_range"
    )
    start, end = _range(value.get("start"), value.get("end"), prefix="validator_range")
    if _range_shape(reference_start, reference_end) != _range_shape(start, end) or not isinstance(
        value.get("compare_formulas"), bool
    ):
        raise SpreadsheetContractError("spreadsheet_validator_reference_range_invalid")
    return {
        "validator_id": validator_id,
        "kind": "reference_range",
        "reference_id": require_id(value.get("reference_id"), "reference_id"),
        "reference_sheet_id": require_id(value.get("reference_sheet_id"), "reference_sheet_id"),
        "reference_start": reference_start,
        "reference_end": reference_end,
        "sheet_id": require_id(value.get("sheet_id"), "sheet_id"),
        "start": start,
        "end": end,
        **_tolerance(value, rounding=False),
        "compare_formulas": value["compare_formulas"],
    }


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _required_number(value: object, field: str) -> float:
    if not _is_finite_number(value):
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return float(value)


def _tolerance(value: Mapping[str, Any], *, rounding: bool = True) -> dict[str, Any]:
    absolute = value.get("absolute_tolerance")
    relative = value.get("relative_tolerance")
    if (
        not _is_finite_number(absolute)
        or not _is_finite_number(relative)
        or float(absolute) < 0
        or not 0 <= float(relative) <= 1
    ):
        raise SpreadsheetContractError("spreadsheet_validator_tolerance_invalid")
    result: dict[str, Any] = {
        "absolute_tolerance": float(absolute),
        "relative_tolerance": float(relative),
    }
    if rounding:
        digits = value.get("rounding_digits")
        if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 15:
            raise SpreadsheetContractError("spreadsheet_validator_rounding_invalid")
        result["rounding_digits"] = digits
    return result


def _range_shape(start: str, end: str) -> tuple[int, int]:
    start_row, start_column = cell_coordinates(start)
    end_row, end_column = cell_coordinates(end)
    return end_row - start_row + 1, end_column - start_column + 1


__all__ = [
    "ACTION_KINDS",
    "FORMULA_OPS",
    "VALIDATOR_KINDS",
    "SpreadsheetContractError",
    "SpreadsheetProposalV1",
    "WorkbookSnapshotV1",
    "canonical_digest",
    "canonical_json",
    "cell_coordinates",
    "require_cell",
    "require_digest",
    "require_id",
    "validate_formula",
    "validate_action",
    "validate_json_scalar",
    "validate_validator",
]
