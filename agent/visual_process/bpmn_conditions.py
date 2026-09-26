"""Compile a bounded comparison dialect to the existing declarative condition DSL.

This module only inspects syntax. It never evaluates Python, FEEL or scripts.
"""

from __future__ import annotations

import ast
import math
import re

from agent.visual_process.bpmn_execution_support import BpmnExecutionError, BpmnExecutionIssue

_PATH = re.compile(r"^(?:input|results|artifacts)(?:\.[A-Za-z_][A-Za-z0-9_-]*)+$")
_OPS = {ast.Eq: "eq", ast.NotEq: "ne", ast.In: "in", ast.Gt: "gt", ast.GtE: "ge", ast.Lt: "lt", ast.LtE: "le"}


def compile_condition(expression: str, *, element_id: str) -> dict:
    try:
        if not isinstance(expression, str) or len(expression) > 4096:
            raise ValueError("expression_size")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 128:
            raise ValueError("expression_complexity")
        return _compile(tree.body, depth=0)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise BpmnExecutionError(
            [
                BpmnExecutionIssue(
                    "bpmn_expression_unsupported",
                    element_id,
                    "Use bounded input/results/artifacts comparisons and and/or/not; no executable code.",
                )
            ]
        ) from exc


def _field(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _field(node.value) + "." + node.attr
    raise ValueError("field_required")


def _literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in {bool, int, float, str, type(None)}:
        if type(node.value) is float and not math.isfinite(node.value):
            raise ValueError("finite_number_required")
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in {int, float}
    ):
        return -_literal(node.operand)
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 64:
        return [_literal(item) for item in node.elts]
    raise ValueError("literal_required")


def _compile(node, *, depth):
    if depth > 12:
        raise ValueError("expression_depth")
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        return {
            "op": "all" if isinstance(node.op, ast.And) else "any",
            "conditions": [_compile(item, depth=depth + 1) for item in node.values],
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return {"op": "not", "condition": _compile(node.operand, depth=depth + 1)}
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _OPS:
        field = _field(node.left)
        if not _PATH.fullmatch(field):
            raise ValueError("field_invalid")
        operator = _OPS[type(node.ops[0])]
        value = _literal(node.comparators[0])
        if operator == "in":
            if not isinstance(value, list) or any(isinstance(item, list) for item in value):
                raise ValueError("flat_collection_required")
        elif isinstance(value, list):
            raise ValueError("scalar_comparison_required")
        return {"op": operator, "field": field, "value": value}
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return {"op": "always"} if node.value else {"op": "not", "condition": {"op": "always"}}
    raise ValueError("expression_unsupported")
