"""Hub-side validation for registry-backed Visual Process step fields.

The node-definition registry is the presentation contract as well as the
allowlisted patch surface.  This validator keeps its declarative constraints
authoritative at the Hub boundary without coupling the graph validator to UI
renderers or individual task kinds.  Unknown additive metadata remains
losslessly readable for compatibility; only fields advertised by a canonical
definition are validated here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.node_definitions import get_node_definition


@dataclass(frozen=True)
class NodeDefinitionFieldViolation:
    """Stable, renderer-independent field validation result."""

    code: str
    message: str
    path: str


class NodeDefinitionStepValidator:
    """Validate one canonical step against its published NodeDefinition."""

    _STRING_TYPES = frozenset({"text", "resource_reference", "expression", "secret_reference"})
    _LIST_TYPES = frozenset({"multi_select", "io_port", "structured_list"})
    _SECRET_REFERENCE = re.compile(r"^env://[A-Z][A-Z0-9_]{1,127}$")

    def validate(
        self,
        step: VisualProcessStep,
        *,
        enforce_static_required: bool = False,
    ) -> list[NodeDefinitionFieldViolation]:
        """Validate configured values.

        Static ``required`` is primarily a form-creation contract because a
        runtime input artifact may supply the same value. Callers that validate
        a newly created form can opt into it. Conditional requirements are
        always enforced because the step itself selected the dependent mode.
        """
        definition = get_node_definition(step.kind)
        if definition is None:
            return []
        payload = step.model_dump(mode="json")
        violations: list[NodeDefinitionFieldViolation] = []
        for field in definition["fields"]:
            path = str(field["path"])
            if not self._condition_matches(payload, field.get("visible_when")):
                continue
            present, value = self._value_at_pointer(payload, path)
            required = (enforce_static_required and bool(field.get("required"))) or self._condition_matches(
                payload, field.get("required_when"), absent_condition_matches=False
            )
            if not present or self._is_empty(value):
                if required:
                    violations.append(
                        self._violation(
                            step,
                            path,
                            "node_field_required",
                            f"Field '{path}' is required by the node definition.",
                        )
                    )
                continue
            violations.extend(self._validate_value(step, field, value))
        return violations

    def _validate_value(
        self,
        step: VisualProcessStep,
        field: Mapping[str, Any],
        value: Any,
    ) -> list[NodeDefinitionFieldViolation]:
        path = str(field["path"])
        field_type = str(field.get("field_type") or "")
        constraints = field.get("constraints")
        constraints = constraints if isinstance(constraints, Mapping) else {}
        invalid_type = False
        if field_type in self._STRING_TYPES:
            invalid_type = not isinstance(value, str)
        elif field_type == "number":
            invalid_type = (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
            )
        elif field_type == "boolean":
            invalid_type = not isinstance(value, bool)
        elif field_type == "enum":
            invalid_type = not isinstance(value, (str, int, float, bool))
        elif field_type in self._LIST_TYPES:
            invalid_type = not isinstance(value, list)
        if invalid_type:
            return [
                self._violation(
                    step,
                    path,
                    "node_field_type_invalid",
                    f"Field '{path}' has an invalid value type for '{field_type}'.",
                )
            ]

        violations: list[NodeDefinitionFieldViolation] = []
        if field_type == "number":
            number = float(value)
            if "minimum" in constraints and number < float(constraints["minimum"]):
                violations.append(
                    self._violation(
                        step,
                        path,
                        "node_field_minimum",
                        f"Field '{path}' is below its minimum.",
                    )
                )
            if "maximum" in constraints and number > float(constraints["maximum"]):
                violations.append(
                    self._violation(
                        step,
                        path,
                        "node_field_maximum",
                        f"Field '{path}' exceeds its maximum.",
                    )
                )
            if constraints.get("integer") is True and not number.is_integer():
                violations.append(
                    self._violation(
                        step,
                        path,
                        "node_field_integer_required",
                        f"Field '{path}' must be an integer.",
                    )
                )

        pattern = constraints.get("pattern")
        if isinstance(pattern, str) and isinstance(value, str):
            try:
                pattern_matches = re.search(pattern, value) is not None
            except re.error:
                pattern_matches = False
            if not pattern_matches:
                violations.append(
                    self._violation(
                        step,
                        path,
                        "node_field_pattern_mismatch",
                        f"Field '{path}' does not match its required format.",
                    )
                )

        options = field.get("options")
        if isinstance(options, list) and options:
            allowed = [item.get("value") for item in options if isinstance(item, Mapping)]
            # Multi-select fields such as policy_hints are deliberately
            # extensible. Their options guide the UI but are not a closed Hub
            # allowlist. Enum values are a closed declarative contract.
            candidates = [value] if field_type == "enum" else []
            if any(candidate not in allowed for candidate in candidates):
                violations.append(
                    self._violation(
                        step,
                        path,
                        "node_field_option_invalid",
                        f"Field '{path}' contains a value outside its declared options.",
                    )
                )

        if (
            field_type == "secret_reference"
            and isinstance(value, str)
            and self._SECRET_REFERENCE.fullmatch(value.strip()) is None
        ):
            violations.append(
                self._violation(
                    step,
                    path,
                    "node_field_secret_reference_invalid",
                    f"Field '{path}' must contain an opaque env reference.",
                )
            )
        return violations

    @classmethod
    def _condition_matches(
        cls,
        payload: Mapping[str, Any],
        raw_condition: Any,
        *,
        absent_condition_matches: bool = True,
    ) -> bool:
        if not isinstance(raw_condition, Mapping):
            return absent_condition_matches
        present, value = cls._value_at_pointer(payload, str(raw_condition.get("path") or ""))
        if "exists" in raw_condition and present != bool(raw_condition["exists"]):
            return False
        if "equals" in raw_condition and value != raw_condition["equals"]:
            return False
        if "equals_any" in raw_condition and value not in raw_condition["equals_any"]:
            return False
        if "not_equals" in raw_condition and value == raw_condition["not_equals"]:
            return False
        if "not_equals_any" in raw_condition and value in raw_condition["not_equals_any"]:
            return False
        return True

    @staticmethod
    def _value_at_pointer(payload: Mapping[str, Any], pointer: str) -> tuple[bool, Any]:
        if not pointer.startswith("/"):
            return False, None
        current: Any = payload
        for raw_segment in pointer.removeprefix("/").split("/"):
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or segment not in current:
                return False, None
            current = current[segment]
        return True, current

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or value == ""

    @staticmethod
    def _violation(
        step: VisualProcessStep,
        field_path: str,
        code: str,
        message: str,
    ) -> NodeDefinitionFieldViolation:
        return NodeDefinitionFieldViolation(
            code=code,
            message=message,
            path=f"/steps/{step.id}{field_path}",
        )


__all__ = ["NodeDefinitionFieldViolation", "NodeDefinitionStepValidator"]
