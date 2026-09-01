"""Small deterministic parser for the closed Spreadsheet Studio formula AST."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_CELL = r"\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})"
_REFERENCE = re.compile(rf"^(?:(?:'((?:[^']|'')+)'|([A-Za-z0-9_. ]+))!)?{_CELL}$", re.IGNORECASE)
_SUM = re.compile(
    rf"^SUM\((?:(?:'((?:[^']|'')+)'|([A-Za-z0-9_. ]+))!)?{_CELL}:{_CELL}\)$",
    re.IGNORECASE,
)


class SpreadsheetFormulaUnsupported(ValueError):
    pass


def parse_formula(value: str, *, current_sheet_id: str, sheet_ids_by_name: Mapping[str, str]) -> dict[str, Any]:
    expression = str(value or "").strip()
    if expression.startswith("="):
        expression = expression[1:]
    return _parse(expression.strip(), current_sheet_id=current_sheet_id, sheet_ids_by_name=sheet_ids_by_name, depth=0)


def render_formula(value: Mapping[str, Any], sheet_names: Mapping[str, str]) -> str:
    """Render a validated closed AST without accepting arbitrary formula text."""

    op = value["op"]
    if op == "literal":
        literal = value["value"]
        if isinstance(literal, str):
            return '"' + literal.replace('"', '""') + '"'
        if literal is True:
            return "TRUE()"
        if literal is False:
            return "FALSE()"
        if literal is None:
            return '""'
        return str(literal)
    if op == "cell":
        name = sheet_names[str(value["sheet_id"])].replace("'", "''")
        return f"'{name}'!{value['cell']}"
    if op == "sum_range":
        name = sheet_names[str(value["sheet_id"])].replace("'", "''")
        return f"SUM('{name}'!{value['start']}:{value['end']})"
    operator = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}[str(op)]
    return f"({render_formula(value['left'], sheet_names)}{operator}{render_formula(value['right'], sheet_names)})"


def _parse(
    expression: str,
    *,
    current_sheet_id: str,
    sheet_ids_by_name: Mapping[str, str],
    depth: int,
) -> dict[str, Any]:
    if depth > 8 or not expression:
        raise SpreadsheetFormulaUnsupported("spreadsheet_formula_unsupported")
    expression = _strip_parentheses(expression)
    for operators in (("+", "-"), ("*", "/")):
        split = _rightmost_operator(expression, operators)
        if split is not None:
            index, operator = split
            return {
                "op": {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}[operator],
                "left": _parse(
                    expression[:index],
                    current_sheet_id=current_sheet_id,
                    sheet_ids_by_name=sheet_ids_by_name,
                    depth=depth + 1,
                ),
                "right": _parse(
                    expression[index + 1 :],
                    current_sheet_id=current_sheet_id,
                    sheet_ids_by_name=sheet_ids_by_name,
                    depth=depth + 1,
                ),
            }
    match = _SUM.fullmatch(expression)
    if match:
        sheet_name = _sheet_name(match.group(1), match.group(2))
        return {
            "op": "sum_range",
            "sheet_id": _sheet_id(sheet_name, current_sheet_id, sheet_ids_by_name),
            "start": f"{match.group(3).upper()}{match.group(4)}",
            "end": f"{match.group(5).upper()}{match.group(6)}",
        }
    match = _REFERENCE.fullmatch(expression)
    if match:
        sheet_name = _sheet_name(match.group(1), match.group(2))
        return {
            "op": "cell",
            "sheet_id": _sheet_id(sheet_name, current_sheet_id, sheet_ids_by_name),
            "cell": f"{match.group(3).upper()}{match.group(4)}",
        }
    if expression.startswith('"') and expression.endswith('"') and len(expression) >= 2:
        return {"op": "literal", "value": expression[1:-1].replace('""', '"')}
    if expression.upper() in {"TRUE()", "FALSE()"}:
        return {"op": "literal", "value": expression.upper() == "TRUE()"}
    try:
        number = float(expression)
    except ValueError as exc:
        raise SpreadsheetFormulaUnsupported("spreadsheet_formula_unsupported") from exc
    return {"op": "literal", "value": int(number) if number.is_integer() else number}


def _strip_parentheses(value: str) -> str:
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        wraps = True
        quoted = False
        for index, character in enumerate(value):
            if character == '"':
                quoted = not quoted
            elif not quoted and character == "(":
                depth += 1
            elif not quoted and character == ")":
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    wraps = False
                    break
        if not wraps or depth != 0:
            break
        value = value[1:-1].strip()
    return value


def _rightmost_operator(value: str, operators: tuple[str, ...]) -> tuple[int, str] | None:
    depth = 0
    quoted = False
    for index in range(len(value) - 1, -1, -1):
        character = value[index]
        if character == '"':
            quoted = not quoted
        elif not quoted and character == ")":
            depth += 1
        elif not quoted and character == "(":
            depth -= 1
        elif not quoted and depth == 0 and character in operators and index > 0:
            return index, character
    return None


def _sheet_name(quoted: str | None, plain: str | None) -> str | None:
    if quoted is not None:
        return quoted.replace("''", "'")
    return plain.strip() if plain is not None else None


def _sheet_id(name: str | None, current: str, mapping: Mapping[str, str]) -> str:
    if name is None:
        return current
    try:
        return mapping[name.casefold()]
    except KeyError as exc:
        raise SpreadsheetFormulaUnsupported("spreadsheet_formula_sheet_unknown") from exc


__all__ = ["SpreadsheetFormulaUnsupported", "parse_formula", "render_formula"]
