from __future__ import annotations

import re


class ExpressionMappingRegistry:
    """Deterministic registry for first-scope expression equivalence."""

    def map_java_expression(self, expression: str, *, target_language: str, nullability: str = "unknown_nullability") -> dict:
        expr = str(expression or "").strip()
        target = str(target_language or "").lower()

        if re.fullmatch(r"\d+(\.\d+)?[LlFfDd]?|true|false|null|\"[^\"]*\"", expr):
            return {"status": "ok", "target_expression": expr, "rule_id": "expr.literal.v1", "warnings": []}

        equals = re.fullmatch(r"(\w+)\.equals\((\w+)\)", expr)
        if equals:
            if nullability != "non_null":
                return {"status": "needs_review", "target_expression": "", "rule_id": "expr.equals.v1", "warnings": ["equals_requires_non_null_receiver"]}
            op = "==" if target == "kotlin" else "==="
            return {"status": "ok", "target_expression": f"{equals.group(1)} {op} {equals.group(2)}", "rule_id": "expr.equals.v1", "warnings": []}

        objects_equals = re.fullmatch(r"Objects\.equals\((\w+),\s*(\w+)\)", expr)
        if objects_equals:
            op = "==" if target == "kotlin" else "==="
            return {"status": "ok", "target_expression": f"{objects_equals.group(1)} {op} {objects_equals.group(2)}", "rule_id": "expr.objects_equals.v1", "warnings": ["null_safe_equality_assumed"]}

        if re.fullmatch(r"!\s*\w+", expr):
            inner = expr[1:].strip()
            return {"status": "ok", "target_expression": f"!{inner}", "rule_id": "expr.boolean_negation.v1", "warnings": []}

        if re.fullmatch(r"\w+\s*==\s*null|\w+\s*!=\s*null", expr):
            return {"status": "needs_review", "target_expression": expr, "rule_id": "expr.null_check.v1", "warnings": ["null_check_semantics_differ_by_target"]}

        if re.fullmatch(r"\w+(?:\.\w+)*", expr):
            return {"status": "ok", "target_expression": expr, "rule_id": "expr.property_access.v1", "warnings": []}

        if re.fullmatch(r"[\w\s+\-*/()<>!=&|.\"]+", expr):
            return {"status": "needs_review", "target_expression": expr, "rule_id": "expr.simple_operator.v1", "warnings": ["operator_semantics_require_review"]}

        return {"status": "needs_review", "target_expression": "", "rule_id": "", "warnings": ["unsupported_expression"]}
