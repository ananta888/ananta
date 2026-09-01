"""Additive rich workbook semantics for Spreadsheet Studio V2."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from ananta_contracts.spreadsheet_studio import (
    SpreadsheetContractError,
    WorkbookSnapshotV1,
    canonical_digest,
    cell_coordinates,
    require_cell,
    require_id,
    validate_formula,
    validate_json_scalar,
)

_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TIMEZONE = re.compile(r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")
_HEX = re.compile(r"^[0-9A-F]{6,8}$")


def _exact(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise SpreadsheetContractError(f"spreadsheet_{name}_fields_invalid")


def _sequence(value: object, field: str, *, maximum: int) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum:
        raise SpreadsheetContractError(f"spreadsheet_{field}_invalid")
    return value


def _scalar(value: object, field: str) -> str | int | float | bool | None:
    return validate_json_scalar(value, field)


@dataclass(frozen=True, slots=True)
class WorkbookSnapshotV2:
    SCHEMA: ClassVar[str] = "ananta.spreadsheet-workbook-snapshot.v2"
    value: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> WorkbookSnapshotV2:
        _exact(
            value,
            {
                "schema",
                "snapshot_id",
                "document_version_id",
                "locale",
                "timezone",
                "date_system",
                "recalc_profile",
                "engine",
                "styles",
                "named_ranges",
                "tables",
                "charts",
                "dependencies",
                "unsupported_objects",
                "sheets",
            },
            "snapshot_v2",
        )
        if value.get("schema") != cls.SCHEMA:
            raise SpreadsheetContractError("spreadsheet_snapshot_v2_invalid")
        locale = str(value.get("locale") or "")
        timezone = str(value.get("timezone") or "")
        if not _LOCALE.fullmatch(locale) or not _TIMEZONE.fullmatch(timezone):
            raise SpreadsheetContractError("spreadsheet_snapshot_environment_invalid")
        if value.get("date_system") not in {"1900", "1904"}:
            raise SpreadsheetContractError("spreadsheet_date_system_invalid")
        recalc_profile = str(value.get("recalc_profile") or "")
        if recalc_profile not in {"automatic", "automatic_except_data_tables", "manual"}:
            raise SpreadsheetContractError("spreadsheet_recalc_profile_invalid")
        engine = value.get("engine")
        if not isinstance(engine, Mapping):
            raise SpreadsheetContractError("spreadsheet_engine_invalid")
        _exact(engine, {"name", "version"}, "engine")
        normalized_engine = {
            "name": require_id(engine.get("name"), "engine_name"),
            "version": require_id(engine.get("version"), "engine_version"),
        }

        styles = _styles(value.get("styles"))
        sheets, sheet_ids, cell_keys = _sheets(value.get("sheets"), style_ids=set(styles))
        named_ranges = _ranges(value.get("named_ranges"), sheet_ids=sheet_ids, kind="named_range", maximum=5_000)
        tables = _tables(value.get("tables"), sheet_ids=sheet_ids)
        charts = _charts(value.get("charts"), sheet_ids=sheet_ids)
        dependencies = _dependencies(value.get("dependencies"), cell_keys=cell_keys)
        unsupported = _unsupported(value.get("unsupported_objects"))
        normalized = {
            "schema": cls.SCHEMA,
            "snapshot_id": require_id(value.get("snapshot_id"), "snapshot_id"),
            "document_version_id": require_id(value.get("document_version_id"), "document_version_id"),
            "locale": locale,
            "timezone": timezone,
            "date_system": value["date_system"],
            "recalc_profile": recalc_profile,
            "engine": normalized_engine,
            "styles": list(styles.values()),
            "named_ranges": named_ranges,
            "tables": tables,
            "charts": charts,
            "dependencies": dependencies,
            "unsupported_objects": unsupported,
            "sheets": sheets,
        }
        return cls(normalized)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.value))

    @property
    def digest(self) -> str:
        return canonical_digest(self.value)

    def execution_v1(self) -> WorkbookSnapshotV1:
        sheets = []
        for sheet in self.value["sheets"]:
            sheets.append(
                {
                    "sheet_id": sheet["sheet_id"],
                    "name": sheet["name"],
                    "hidden": sheet["visibility"] != "visible",
                    "cells": [
                        {
                            "address": cell["address"],
                            "value": cell["raw_value"],
                            "formula": copy.deepcopy(cell["formula_ast"]),
                            "style_ref": cell["style_id"],
                        }
                        for cell in sheet["cells"]
                    ],
                }
            )
        return WorkbookSnapshotV1.from_mapping(
            {
                "schema": WorkbookSnapshotV1.SCHEMA,
                "snapshot_id": self.value["snapshot_id"],
                "document_version_id": self.value["document_version_id"],
                "sheets": sheets,
            }
        )


def parse_workbook_snapshot(value: Mapping[str, Any]) -> WorkbookSnapshotV1 | WorkbookSnapshotV2:
    if value.get("schema") == WorkbookSnapshotV2.SCHEMA:
        return WorkbookSnapshotV2.from_mapping(value)
    return WorkbookSnapshotV1.from_mapping(value)


def execution_snapshot(value: Mapping[str, Any] | WorkbookSnapshotV1 | WorkbookSnapshotV2) -> WorkbookSnapshotV1:
    parsed = parse_workbook_snapshot(value) if isinstance(value, Mapping) else value
    return parsed.execution_v1() if isinstance(parsed, WorkbookSnapshotV2) else parsed


def merge_execution_candidate(
    *,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    engine_name: str,
    engine_version: str,
) -> WorkbookSnapshotV1 | WorkbookSnapshotV2:
    parsed_base = parse_workbook_snapshot(base)
    parsed_candidate = WorkbookSnapshotV1.from_mapping(candidate)
    if isinstance(parsed_base, WorkbookSnapshotV1):
        return parsed_candidate
    rich = parsed_base.to_dict()
    rich["snapshot_id"] = parsed_candidate.snapshot_id
    rich["engine"] = {
        "name": require_id(engine_name or "unknown-engine", "engine_name"),
        "version": require_id(engine_version or "unknown-version", "engine_version"),
    }
    _rebase_objects(rich, actions)
    style_ids = {style["style_id"] for style in rich["styles"]}
    for action in actions:
        if action.get("kind") == "format_range":
            style_id = f"style-{canonical_digest(action['style'])[:24]}"
            if style_id not in style_ids:
                style = dict(action["style"])
                rich["styles"].append({"style_id": style_id, **style})
                style_ids.add(style_id)
    old_cells = {
        (sheet["sheet_id"], cell["address"]): cell for sheet in parsed_base.value["sheets"] for cell in sheet["cells"]
    }
    names = {sheet["sheet_id"]: sheet["name"] for sheet in rich["sheets"]}
    candidate_by_sheet = {sheet["sheet_id"]: sheet for sheet in parsed_candidate.sheets}
    for rich_sheet in rich["sheets"]:
        sheet_id = rich_sheet["sheet_id"]
        cells = []
        for cell in candidate_by_sheet[sheet_id]["cells"]:
            row, column = cell_coordinates(cell["address"])
            previous = old_cells.get((sheet_id, cell["address"]), {})
            formula_ast = copy.deepcopy(cell["formula"])
            formula_text = _render_formula(formula_ast, names) if formula_ast is not None else None
            cells.append(
                {
                    "row": row,
                    "column": column,
                    "address": cell["address"],
                    "raw_value": cell["value"],
                    "displayed_value": str(cell["value"]) if cell["value"] is not None else None,
                    "formula_text": formula_text,
                    "formula_ast": formula_ast,
                    "style_id": cell["style_ref"],
                }
            )
            if formula_ast == previous.get("formula_ast") and previous.get("formula_text"):
                cells[-1]["formula_text"] = previous["formula_text"]
                cells[-1]["displayed_value"] = previous.get("displayed_value")
        rich_sheet["cells"] = cells
    rich["dependencies"] = _derive_dependencies(rich["sheets"])
    return WorkbookSnapshotV2.from_mapping(rich)


def _styles(value: object) -> dict[str, dict[str, Any]]:
    result = {}
    for item in _sequence(value, "styles", maximum=10_000):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError("spreadsheet_style_invalid")
        _exact(item, {"style_id", "number_format", "bold", "italic", "fill_color"}, "style")
        style_id = require_id(item.get("style_id"), "style_id")
        number_format = item.get("number_format")
        fill = item.get("fill_color")
        if style_id in result or (
            number_format is not None and (not isinstance(number_format, str) or len(number_format) > 128)
        ):
            raise SpreadsheetContractError("spreadsheet_style_invalid")
        if fill is not None and (not isinstance(fill, str) or not _HEX.fullmatch(fill)):
            raise SpreadsheetContractError("spreadsheet_style_fill_invalid")
        if any(item.get(field) is not None and not isinstance(item.get(field), bool) for field in ("bold", "italic")):
            raise SpreadsheetContractError("spreadsheet_style_invalid")
        result[style_id] = {
            "style_id": style_id,
            "number_format": number_format,
            "bold": item.get("bold"),
            "italic": item.get("italic"),
            "fill_color": fill,
        }
    return result


def _sheets(value: object, *, style_ids: set[str]) -> tuple[list[dict[str, Any]], set[str], set[tuple[str, str]]]:
    raw_sheets = _sequence(value, "sheets", maximum=64)
    if not raw_sheets:
        raise SpreadsheetContractError("spreadsheet_sheet_count_invalid")
    sheets = []
    ids: set[str] = set()
    cell_keys: set[tuple[str, str]] = set()
    total = 0
    for raw in raw_sheets:
        if not isinstance(raw, Mapping):
            raise SpreadsheetContractError("spreadsheet_sheet_invalid")
        _exact(raw, {"sheet_id", "name", "visibility", "cells"}, "sheet_v2")
        sheet_id = require_id(raw.get("sheet_id"), "sheet_id")
        name = str(raw.get("name") or "").strip()
        if (
            sheet_id in ids
            or not 1 <= len(name) <= 128
            or raw.get("visibility")
            not in {
                "visible",
                "hidden",
                "very_hidden",
            }
        ):
            raise SpreadsheetContractError("spreadsheet_sheet_invalid")
        cells = []
        for cell in _sequence(raw.get("cells"), "cells", maximum=100_000):
            if not isinstance(cell, Mapping):
                raise SpreadsheetContractError("spreadsheet_cell_invalid")
            _exact(
                cell,
                {"row", "column", "address", "raw_value", "displayed_value", "formula_text", "formula_ast", "style_id"},
                "cell_v2",
            )
            address = require_cell(cell.get("address"))
            row, column = cell_coordinates(address)
            if cell.get("row") != row or cell.get("column") != column or (sheet_id, address) in cell_keys:
                raise SpreadsheetContractError("spreadsheet_cell_coordinate_binding_invalid")
            formula_text = cell.get("formula_text")
            if formula_text is not None and (
                not isinstance(formula_text, str) or not formula_text.startswith("=") or len(formula_text) > 8_192
            ):
                raise SpreadsheetContractError("spreadsheet_formula_text_invalid")
            formula_ast = validate_formula(cell["formula_ast"]) if cell.get("formula_ast") is not None else None
            style_id = require_id(cell.get("style_id"), "style_id") if cell.get("style_id") else None
            if style_id is not None and style_id not in style_ids:
                raise SpreadsheetContractError("spreadsheet_cell_style_unknown")
            displayed = cell.get("displayed_value")
            if displayed is not None and (not isinstance(displayed, str) or len(displayed.encode()) > 16_384):
                raise SpreadsheetContractError("spreadsheet_displayed_value_invalid")
            cells.append(
                {
                    "row": row,
                    "column": column,
                    "address": address,
                    "raw_value": _scalar(cell.get("raw_value"), "raw_value"),
                    "displayed_value": displayed,
                    "formula_text": formula_text,
                    "formula_ast": formula_ast,
                    "style_id": style_id,
                }
            )
            cell_keys.add((sheet_id, address))
        total += len(cells)
        if total > 100_000:
            raise SpreadsheetContractError("spreadsheet_cell_limit_exceeded")
        sheets.append({"sheet_id": sheet_id, "name": name, "visibility": raw["visibility"], "cells": cells})
        ids.add(sheet_id)
    return sheets, ids, cell_keys


def _ranges(value: object, *, sheet_ids: set[str], kind: str, maximum: int) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for item in _sequence(value, f"{kind}s", maximum=maximum):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError(f"spreadsheet_{kind}_invalid")
        fields = {f"{kind}_id", "name", "sheet_id", "start", "end"}
        _exact(item, fields, kind)
        object_id = require_id(item.get(f"{kind}_id"), f"{kind}_id")
        sheet_id = require_id(item.get("sheet_id"), "sheet_id")
        start, end = require_cell(item.get("start")), require_cell(item.get("end"))
        if object_id in ids or sheet_id not in sheet_ids or cell_coordinates(start) > cell_coordinates(end):
            raise SpreadsheetContractError(f"spreadsheet_{kind}_invalid")
        name = str(item.get("name") or "").strip()
        if not 1 <= len(name) <= 128:
            raise SpreadsheetContractError(f"spreadsheet_{kind}_invalid")
        result.append({f"{kind}_id": object_id, "name": name, "sheet_id": sheet_id, "start": start, "end": end})
        ids.add(object_id)
    return result


def _tables(value: object, *, sheet_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for item in _sequence(value, "tables", maximum=1_000):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError("spreadsheet_table_invalid")
        _exact(item, {"table_id", "name", "sheet_id", "start", "end", "header_row"}, "table")
        table_id = require_id(item.get("table_id"), "table_id")
        sheet_id = require_id(item.get("sheet_id"), "sheet_id")
        start, end = require_cell(item.get("start")), require_cell(item.get("end"))
        name = str(item.get("name") or "").strip()
        if (
            table_id in ids
            or sheet_id not in sheet_ids
            or cell_coordinates(start) > cell_coordinates(end)
            or not 1 <= len(name) <= 128
            or not isinstance(item.get("header_row"), bool)
        ):
            raise SpreadsheetContractError("spreadsheet_table_invalid")
        result.append(
            {
                "table_id": table_id,
                "name": name,
                "sheet_id": sheet_id,
                "start": start,
                "end": end,
                "header_row": item["header_row"],
            }
        )
        ids.add(table_id)
    return result


def _charts(value: object, *, sheet_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for item in _sequence(value, "charts", maximum=1_000):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError("spreadsheet_chart_invalid")
        _exact(item, {"chart_id", "kind", "sheet_id", "anchor", "source_start", "source_end"}, "chart")
        chart_id = require_id(item.get("chart_id"), "chart_id")
        sheet_id = require_id(item.get("sheet_id"), "sheet_id")
        if chart_id in ids or sheet_id not in sheet_ids or item.get("kind") not in {"bar", "line", "pie", "scatter"}:
            raise SpreadsheetContractError("spreadsheet_chart_invalid")
        result.append(
            {
                "chart_id": chart_id,
                "kind": item["kind"],
                "sheet_id": sheet_id,
                "anchor": require_cell(item.get("anchor")),
                "source_start": require_cell(item.get("source_start")),
                "source_end": require_cell(item.get("source_end")),
            }
        )
        ids.add(chart_id)
    return result


def _dependencies(value: object, *, cell_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in _sequence(value, "dependencies", maximum=100_000):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError("spreadsheet_dependency_invalid")
        _exact(item, {"from_sheet_id", "from_cell", "to_sheet_id", "to_cell", "kind"}, "dependency")
        normalized = {
            "from_sheet_id": require_id(item.get("from_sheet_id"), "sheet_id"),
            "from_cell": require_cell(item.get("from_cell")),
            "to_sheet_id": require_id(item.get("to_sheet_id"), "sheet_id"),
            "to_cell": require_cell(item.get("to_cell")),
            "kind": str(item.get("kind") or ""),
        }
        key = tuple(normalized.values())
        if (
            normalized["kind"] not in {"cell", "range"}
            or (normalized["to_sheet_id"], normalized["to_cell"]) not in cell_keys
            or key in seen
        ):
            raise SpreadsheetContractError("spreadsheet_dependency_invalid")
        result.append(normalized)
        seen.add(key)
    return result


def _unsupported(value: object) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for item in _sequence(value, "unsupported_objects", maximum=1_000):
        if not isinstance(item, Mapping):
            raise SpreadsheetContractError("spreadsheet_unsupported_object_invalid")
        _exact(item, {"object_id", "kind", "reason_code"}, "unsupported_object")
        object_id = require_id(item.get("object_id"), "object_id")
        if object_id in ids:
            raise SpreadsheetContractError("spreadsheet_unsupported_object_invalid")
        result.append(
            {
                "object_id": object_id,
                "kind": require_id(item.get("kind"), "unsupported_kind"),
                "reason_code": require_id(item.get("reason_code"), "reason_code"),
            }
        )
        ids.add(object_id)
    return result


def _rebase_objects(snapshot: dict[str, Any], actions: Sequence[Mapping[str, Any]]) -> None:
    for action in actions:
        kind = str(action.get("kind") or "")
        if kind not in {"insert_rows", "delete_rows", "insert_columns", "delete_columns"}:
            continue
        sheet_id = str(action["sheet_id"])
        rows = kind.endswith("rows")
        deleting = kind.startswith("delete")
        start = int(action["start_row"] if rows else action["start_column"])
        count = int(action["count"])
        for collection in ("named_ranges", "tables"):
            retained = []
            for item in snapshot[collection]:
                if item["sheet_id"] != sheet_id:
                    retained.append(item)
                    continue
                shifted_start = _shift_cell(item["start"], rows=rows, start=start, count=count, deleting=deleting)
                shifted_end = _shift_cell(item["end"], rows=rows, start=start, count=count, deleting=deleting)
                if shifted_start is not None and shifted_end is not None:
                    item["start"], item["end"] = shifted_start, shifted_end
                    retained.append(item)
            snapshot[collection] = retained
        retained_charts = []
        for chart in snapshot["charts"]:
            if chart["sheet_id"] != sheet_id:
                retained_charts.append(chart)
                continue
            valid = True
            for field in ("anchor", "source_start", "source_end"):
                shifted = _shift_cell(chart[field], rows=rows, start=start, count=count, deleting=deleting)
                if shifted is None:
                    valid = False
                    break
                chart[field] = shifted
            if valid:
                retained_charts.append(chart)
            else:
                snapshot["unsupported_objects"].append(
                    {
                        "object_id": chart["chart_id"],
                        "kind": "chart",
                        "reason_code": "spreadsheet_chart_range_deleted",
                    }
                )
        snapshot["charts"] = retained_charts


def _shift_cell(address: str, *, rows: bool, start: int, count: int, deleting: bool) -> str | None:
    row, column = cell_coordinates(address)
    coordinate = row if rows else column
    if deleting and start <= coordinate < start + count:
        return None
    if coordinate >= start + (count if deleting else 0):
        coordinate += -count if deleting else count
    if rows:
        row = coordinate
    else:
        column = coordinate
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _derive_dependencies(sheets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for sheet in sheets:
        for cell in sheet["cells"]:
            for reference in _formula_references(cell.get("formula_ast")):
                result.append(
                    {
                        "from_sheet_id": reference[0],
                        "from_cell": reference[1],
                        "to_sheet_id": sheet["sheet_id"],
                        "to_cell": cell["address"],
                        "kind": reference[2],
                    }
                )
    return sorted(result, key=lambda item: tuple(item.values()))


def _formula_references(value: object) -> list[tuple[str, str, str]]:
    if not isinstance(value, Mapping):
        return []
    if value.get("op") == "cell":
        return [(str(value["sheet_id"]), str(value["cell"]), "cell")]
    if value.get("op") in {"sum_range", "average_range", "min_range", "max_range"}:
        return [(str(value["sheet_id"]), str(value["start"]), "range")]
    if value.get("op") == "negate":
        return _formula_references(value.get("expression"))
    if value.get("op") == "if":
        return (
            _formula_references(value.get("condition"))
            + _formula_references(value.get("then"))
            + _formula_references(value.get("else"))
        )
    return _formula_references(value.get("left")) + _formula_references(value.get("right"))


def _render_formula(value: Mapping[str, Any], sheet_names: Mapping[str, str]) -> str:
    op = value["op"]
    if op == "literal":
        literal = value["value"]
        if isinstance(literal, str):
            return '="' + literal.replace('"', '""') + '"'
        return "=" + ("TRUE()" if literal is True else "FALSE()" if literal is False else str(literal or 0))
    name = str(sheet_names[str(value["sheet_id"])]).replace("'", "''") if "sheet_id" in value else ""
    if op == "cell":
        return f"='{name}'!{value['cell']}"
    if op in {"sum_range", "average_range", "min_range", "max_range"}:
        function = {
            "sum_range": "SUM",
            "average_range": "AVERAGE",
            "min_range": "MIN",
            "max_range": "MAX",
        }[str(op)]
        return f"={function}('{name}'!{value['start']}:{value['end']})"
    if op == "negate":
        return f"=-({_render_formula(value['expression'], sheet_names).lstrip('=')})"
    if op == "if":
        condition = _render_formula(value["condition"], sheet_names).lstrip("=")
        then = _render_formula(value["then"], sheet_names).lstrip("=")
        otherwise = _render_formula(value["else"], sheet_names).lstrip("=")
        return f"=IF({condition},{then},{otherwise})"
    operator = {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "divide": "/",
        "equal": "=",
        "not_equal": "<>",
        "less_than": "<",
        "less_equal": "<=",
        "greater_than": ">",
        "greater_equal": ">=",
    }[str(op)]
    left = _render_formula(value["left"], sheet_names).lstrip("=")
    right = _render_formula(value["right"], sheet_names).lstrip("=")
    return f"=({left}{operator}{right})"


__all__ = [
    "WorkbookSnapshotV2",
    "execution_snapshot",
    "merge_execution_candidate",
    "parse_workbook_snapshot",
]
