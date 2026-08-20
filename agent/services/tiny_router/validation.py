"""Strict normalization and schema validation for untrusted model output."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

from agent.services.tiny_router.types import (
    STATUS_ABSTAIN, ToolCallCandidate, TinyActionModelProfile, ValidationResult,
)

_MARKER = re.escape(chr(96) * 3)
_FENCE = re.compile(r"^\s*" + _MARKER + r"(?:json)?\s*(.*?)\s*" + _MARKER + r"\s*$", re.DOTALL)


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey("duplicate_json_key:" + key)
        result[key] = value
    return result


def _decode_payload(payload: Any) -> Any:
    if not isinstance(payload, str):
        return payload
    raw = payload.strip()
    match = _FENCE.match(raw)
    if match:
        raw = match.group(1).strip()
    return json.loads(raw, object_pairs_hook=_unique_object)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_json_value(
    value: Any, schema: Mapping[str, Any], *, path: str = "$",
) -> tuple[str, ...]:
    """Validate the registry schema subset and reject unknown object fields."""
    issues: list[str] = []
    if not isinstance(schema, Mapping):
        return (path + ":schema_not_object",)
    if "$ref" in schema:
        return (path + ":unresolved_schema_ref",)
    if "const" in schema and value != schema["const"]:
        issues.append(path + ":const_mismatch")
    if "enum" in schema:
        enum = schema.get("enum")
        if not isinstance(enum, list) or value not in enum:
            issues.append(path + ":enum_mismatch")
    for keyword in ("oneOf", "anyOf"):
        alternatives = schema.get(keyword)
        if alternatives is not None:
            if not isinstance(alternatives, list) or not alternatives:
                issues.append(path + ":" + keyword + "_invalid")
            else:
                matches = sum(
                    not validate_json_value(value, option, path=path)
                    for option in alternatives if isinstance(option, Mapping)
                )
                if (keyword == "oneOf" and matches != 1) or (
                    keyword == "anyOf" and matches < 1
                ):
                    issues.append(path + ":" + keyword + "_mismatch")
    declared_type = schema.get("type")
    if isinstance(declared_type, list):
        if not any(_type_matches(value, str(item)) for item in declared_type):
            issues.append(path + ":type_mismatch")
            return tuple(issues)
    elif declared_type and not _type_matches(value, str(declared_type)):
        issues.append(path + ":type_mismatch")
        return tuple(issues)
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        if not isinstance(properties, Mapping):
            return tuple(issues + [path + ":properties_not_object"])
        required = schema.get("required") or []
        if not isinstance(required, list):
            issues.append(path + ":required_not_array")
            required = []
        for key in required:
            if key not in value:
                issues.append(path + ":missing_required:" + str(key))
        additional = schema.get("additionalProperties", False)
        for key, item in value.items():
            child = str(key)
            if child in properties:
                issues.extend(validate_json_value(item, properties[child], path=path + "." + child))
            elif isinstance(additional, Mapping):
                issues.extend(validate_json_value(item, additional, path=path + "." + child))
            else:
                issues.append(path + ":unknown_property:" + child)
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            issues.append(path + ":min_items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            issues.append(path + ":max_items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                issues.append(path + ":unique_items")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                issues.extend(validate_json_value(item, items, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            issues.append(path + ":min_length")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            issues.append(path + ":max_length")
        if "pattern" in schema:
            try:
                if re.search(str(schema["pattern"]), value) is None:
                    issues.append(path + ":pattern")
            except re.error:
                issues.append(path + ":invalid_schema_pattern")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            issues.append(path + ":non_finite_number")
        if "minimum" in schema and numeric < float(schema["minimum"]):
            issues.append(path + ":minimum")
        if "maximum" in schema and numeric > float(schema["maximum"]):
            issues.append(path + ":maximum")
        if "exclusiveMinimum" in schema and numeric <= float(schema["exclusiveMinimum"]):
            issues.append(path + ":exclusive_minimum")
        if "exclusiveMaximum" in schema and numeric >= float(schema["exclusiveMaximum"]):
            issues.append(path + ":exclusive_maximum")
    return tuple(issues)


class CandidateValidator:
    def validate(
        self, payload: Any, *, tools: Sequence[Mapping[str, Any]],
        profile: TinyActionModelProfile, adapter_id: str,
        min_confidence: float | None = None,
    ) -> ValidationResult:
        try:
            decoded = _decode_payload(payload)
        except (_DuplicateKey, json.JSONDecodeError, TypeError, ValueError) as exc:
            return ValidationResult("invalid", reason_code="invalid_json", issues=(str(exc),))
        if not isinstance(decoded, Mapping):
            return ValidationResult("invalid", reason_code="output_not_object")
        raw_calls = self._calls(decoded)
        if raw_calls is None:
            return ValidationResult("invalid", reason_code="call_contract_missing")
        if not raw_calls:
            return ValidationResult(STATUS_ABSTAIN, reason_code="model_abstained")
        if len(raw_calls) != 1:
            return ValidationResult("invalid", reason_code="multiple_calls_not_supported")
        name, arguments, call_confidence, issue = self._normalize_call(raw_calls[0], decoded)
        if issue:
            return ValidationResult("invalid", reason_code=issue)
        schemas = self._allowed_schemas(tools)
        if name not in schemas:
            return ValidationResult("invalid", reason_code="unknown_or_denied_tool")
        if not isinstance(arguments, dict):
            return ValidationResult("invalid", reason_code="arguments_must_be_object")
        issues = validate_json_value(arguments, schemas[name])
        if issues:
            return ValidationResult("invalid", reason_code="arguments_failed_schema", issues=issues)
        confidence = self._confidence(call_confidence)
        if call_confidence is not None and confidence is None:
            return ValidationResult("invalid", reason_code="confidence_invalid")
        threshold = profile.min_confidence if min_confidence is None else float(min_confidence)
        if profile.supports_confidence and confidence is None:
            return ValidationResult(STATUS_ABSTAIN, reason_code="confidence_missing")
        if confidence is not None and confidence < threshold:
            return ValidationResult(STATUS_ABSTAIN, reason_code="below_confidence_threshold")
        return ValidationResult(
            "valid",
            candidate=ToolCallCandidate(name, arguments, confidence, profile.profile_id, adapter_id),
            reason_code="candidate_validated",
        )

    @staticmethod
    def _calls(payload: Mapping[str, Any]) -> list[Any] | None:
        if "function_calls" in payload:
            calls = payload.get("function_calls")
        elif "tool_calls" in payload:
            calls = payload.get("tool_calls")
        elif "tool" in payload or "name" in payload:
            calls = [payload]
        elif payload.get("type") in {"respond", "refusal"}:
            calls = []
        else:
            return None
        return calls if isinstance(calls, list) else None

    @staticmethod
    def _normalize_call(
        raw_call: Any, payload: Mapping[str, Any],
    ) -> tuple[str, Any, Any, str]:
        if not isinstance(raw_call, Mapping):
            return "", None, None, "call_must_be_object"
        function = raw_call.get("function")
        source = function if isinstance(function, Mapping) else raw_call
        name = str(source.get("name") or source.get("tool") or "").strip()
        if not name or any(char.isspace() for char in name):
            return "", None, None, "tool_name_invalid"
        arguments = source.get("arguments", source.get("args", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments, object_pairs_hook=_unique_object)
            except (_DuplicateKey, json.JSONDecodeError, TypeError, ValueError):
                return name, None, None, "arguments_invalid_json"
        return name, arguments, raw_call.get("confidence", payload.get("confidence")), ""

    @staticmethod
    def _confidence(raw: Any) -> float | None:
        if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        value = float(raw)
        return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None

    @staticmethod
    def _allowed_schemas(
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for item in tools:
            function = item.get("function") if isinstance(item, Mapping) else None
            if not isinstance(function, Mapping):
                continue
            name = str(function.get("name") or "").strip()
            parameters = function.get("parameters")
            if name and isinstance(parameters, Mapping) and name not in result:
                result[name] = parameters
        return result
