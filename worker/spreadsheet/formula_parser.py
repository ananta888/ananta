"""Small deterministic parser for the closed Spreadsheet Studio formula AST."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_CELL = r"\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})"
_REFERENCE = re.compile(rf"^(?:(?:'((?:[^']|'')+)'|([A-Za-z0-9_. ]+))!)?{_CELL}$", re.IGNORECASE)
_RANGE_FUNCTION = re.compile(
    rf"^(SUM|AVERAGE|MIN|MAX)\((?:(?:'((?:[^']|'')+)'|([A-Za-z0-9_. ]+))!)?{_CELL}:{_CELL}\)$",
    re.IGNORECASE,
)


class SpreadsheetFormulaUnsupported(ValueError):
    pass


def parse_formula(value: str, *, current_sheet_id: str, sheet_ids_by_name: Mapping[str, str]) -> dict[str, Any]:
    expression = str(value or "").strip()
    if len(expression) > 8_192:
        raise SpreadsheetFormulaUnsupported("spreadsheet_formula_unsupported")
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
    if op in {"sum_range", "average_range", "min_range", "max_range"}:
        name = sheet_names[str(value["sheet_id"])].replace("'", "''")
        function = {
            "sum_range": "SUM",
            "average_range": "AVERAGE",
            "min_range": "MIN",
            "max_range": "MAX",
        }[str(op)]
        return f"{function}('{name}'!{value['start']}:{value['end']})"
    if op == "negate":
        return f"(-{render_formula(value['expression'], sheet_names)})"
    if op == "if":
        return (
            f"IF({render_formula(value['condition'], sheet_names)},"
            f"{render_formula(value['then'], sheet_names)},{render_formula(value['else'], sheet_names)})"
        )
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
    conditional = _function_arguments(expression, "IF", count=3)
    if conditional is not None:
        return {
            "op": "if",
            "condition": _parse(
                conditional[0], current_sheet_id=current_sheet_id, sheet_ids_by_name=sheet_ids_by_name, depth=depth + 1
            ),
            "then": _parse(
                conditional[1], current_sheet_id=current_sheet_id, sheet_ids_by_name=sheet_ids_by_name, depth=depth + 1
            ),
            "else": _parse(
                conditional[2], current_sheet_id=current_sheet_id, sheet_ids_by_name=sheet_ids_by_name, depth=depth + 1
            ),
        }
    for operator, op in (
        ("<>", "not_equal"),
        ("<=", "less_equal"),
        (">=", "greater_equal"),
        ("=", "equal"),
        ("<", "less_than"),
        (">", "greater_than"),
    ):
        split = _top_level_token(expression, operator)
        if split is not None:
            return {
                "op": op,
                "left": _parse(
                    expression[:split],
                    current_sheet_id=current_sheet_id,
                    sheet_ids_by_name=sheet_ids_by_name,
                    depth=depth + 1,
                ),
                "right": _parse(
                    expression[split + len(operator) :],
                    current_sheet_id=current_sheet_id,
                    sheet_ids_by_name=sheet_ids_by_name,
                    depth=depth + 1,
                ),
            }
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
    if expression.startswith("-"):
        return {
            "op": "negate",
            "expression": _parse(
                expression[1:], current_sheet_id=current_sheet_id, sheet_ids_by_name=sheet_ids_by_name, depth=depth + 1
            ),
        }
    match = _RANGE_FUNCTION.fullmatch(expression)
    if match:
        sheet_name = _sheet_name(match.group(2), match.group(3))
        return {
            "op": {"SUM": "sum_range", "AVERAGE": "average_range", "MIN": "min_range", "MAX": "max_range"}[
                match.group(1).upper()
            ],
            "sheet_id": _sheet_id(sheet_name, current_sheet_id, sheet_ids_by_name),
            "start": f"{match.group(4).upper()}{match.group(5)}",
            "end": f"{match.group(6).upper()}{match.group(7)}",
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
        for index, character in _unquoted_characters(value):
            if character == "(":
                depth += 1
            elif character == ")":
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
    result = None
    for index, character in _unquoted_characters(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and character in operators and not _is_unary_operator(value, index, character):
            result = (index, character)
    return result


def _top_level_token(value: str, token: str) -> int | None:
    depth = 0
    for index, character in _unquoted_characters(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and value.startswith(token, index):
            return index
    return None


def _function_arguments(value: str, name: str, *, count: int) -> list[str] | None:
    prefix = f"{name}("
    if not value.upper().startswith(prefix) or not value.endswith(")"):
        return None
    body = value[len(prefix) : -1]
    parts = []
    start = 0
    depth = 0
    for index, character in _unquoted_characters(body):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and character in {",", ";"}:
            parts.append(body[start:index].strip())
            start = index + 1
    parts.append(body[start:].strip())
    return parts if len(parts) == count and all(parts) else None


def _unquoted_characters(value: str):
    single_quoted = False
    double_quoted = False
    index = 0
    while index < len(value):
        character = value[index]
        if single_quoted:
            if character == "'" and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            if character == "'":
                single_quoted = False
        elif double_quoted:
            if character == '"' and index + 1 < len(value) and value[index + 1] == '"':
                index += 2
                continue
            if character == '"':
                double_quoted = False
        elif character == "'":
            single_quoted = True
        elif character == '"':
            double_quoted = True
        else:
            yield index, character
        index += 1


def _is_unary_operator(value: str, index: int, operator: str) -> bool:
    if index == 0:
        return True
    if operator not in {"+", "-"}:
        return False
    return value[index - 1] in "+-*/(=<>;,"


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
