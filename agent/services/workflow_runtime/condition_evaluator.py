"""Side-effect-free evaluator for execution-plan routing conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_FIELD_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$")
_UNKNOWN = object()


@dataclass(frozen=True)
class ConditionResult:
    value: bool | None
    reason_code: str

    @property
    def matches(self) -> bool:
        return self.value is True


class DeclarativeConditionEvaluator:
    """Evaluate the small condition DSL; arbitrary code is never accepted."""

    def __init__(self, *, maximum_depth: int = 16, maximum_nodes: int = 256) -> None:
        self._maximum_depth = max(1, min(int(maximum_depth), 64))
        self._maximum_nodes = max(1, min(int(maximum_nodes), 4096))

    def evaluate(self, condition: dict[str, Any], state: dict[str, Any]) -> ConditionResult:
        return self._evaluate(condition, state, depth=0, remaining=[self._maximum_nodes])

    def _evaluate(
        self,
        condition: dict[str, Any],
        state: dict[str, Any],
        *,
        depth: int,
        remaining: list[int],
    ) -> ConditionResult:
        if not isinstance(condition, dict):
            return ConditionResult(None, "condition_mapping_required")
        if depth > self._maximum_depth:
            return ConditionResult(None, "condition_depth_exceeded")
        remaining[0] -= 1
        if remaining[0] < 0:
            return ConditionResult(None, "condition_node_limit_exceeded")
        operator = str(condition.get("op") or "").strip()
        if operator == "always":
            return ConditionResult(True, "condition_always")
        if operator in {"all", "any"}:
            children = condition.get("conditions")
            if not isinstance(children, list) or not children:
                return ConditionResult(None, "condition_children_required")
            results = [
                self._evaluate(child, state, depth=depth + 1, remaining=remaining)
                for child in children
            ]
            bounded_failure = next(
                (
                    result
                    for result in results
                    if result.reason_code in {"condition_depth_exceeded", "condition_node_limit_exceeded"}
                ),
                None,
            )
            if bounded_failure is not None:
                return bounded_failure
            if operator == "all":
                if any(result.value is False for result in results):
                    return ConditionResult(False, "condition_all_false")
                if any(result.value is None for result in results):
                    return ConditionResult(None, "condition_all_unknown")
                return ConditionResult(True, "condition_all_true")
            if any(result.value is True for result in results):
                return ConditionResult(True, "condition_any_true")
            if any(result.value is None for result in results):
                return ConditionResult(None, "condition_any_unknown")
            return ConditionResult(False, "condition_any_false")
        if operator == "not":
            child = condition.get("condition")
            if not isinstance(child, dict):
                return ConditionResult(None, "condition_child_required")
            result = self._evaluate(child, state, depth=depth + 1, remaining=remaining)
            if result.value is None:
                if result.reason_code in {"condition_depth_exceeded", "condition_node_limit_exceeded"}:
                    return result
                return ConditionResult(None, "condition_not_unknown")
            return ConditionResult(not result.value, "condition_not_evaluated")
        if operator not in {"eq", "ne", "in", "exists"}:
            return ConditionResult(None, "condition_operator_invalid")

        field_name = str(condition.get("field") or "").strip()
        if not _FIELD_PATH.fullmatch(field_name):
            return ConditionResult(None, "condition_field_invalid")
        actual = self._resolve(state, field_name)
        if operator == "exists":
            return ConditionResult(actual is not _UNKNOWN, "condition_exists_evaluated")
        if actual is _UNKNOWN:
            return ConditionResult(None, "condition_field_unknown")
        if "value" not in condition:
            return ConditionResult(None, "condition_value_required")
        expected = condition["value"]
        if operator == "in":
            if not isinstance(expected, list):
                return ConditionResult(None, "condition_in_collection_required")
            if any(type(item) is not type(actual) for item in expected):
                return ConditionResult(None, "condition_type_mismatch")
            return ConditionResult(actual in expected, "condition_in_evaluated")
        if type(actual) is not type(expected):
            return ConditionResult(None, "condition_type_mismatch")
        if operator == "eq":
            return ConditionResult(actual == expected, "condition_eq_evaluated")
        return ConditionResult(actual != expected, "condition_ne_evaluated")

    @staticmethod
    def _resolve(state: dict[str, Any], field_name: str) -> Any:
        current: Any = state
        for segment in field_name.split("."):
            if not isinstance(current, dict) or segment not in current:
                return _UNKNOWN
            current = current[segment]
        return current
