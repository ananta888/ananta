"""Closed data views for Hub-compiled BPMN scopes; never an authority grant."""

from __future__ import annotations

import json
import re
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json

PROJECTION_KEY = "bpmn_input_projection"
PROJECTION_SCHEMA = "ananta.bpmn_input_projection.v1"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
_RESERVED = frozenset({"__proto__", "prototype", "constructor"})


def validate_input_projection(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) != {"schema", "workflow_input", "dependency_results"}:
        return ("bpmn_input_projection_invalid",)
    if value["schema"] != PROJECTION_SCHEMA:
        return ("bpmn_input_projection_schema_unsupported",)
    for group in ("workflow_input", "dependency_results"):
        fields = value[group]
        if not isinstance(fields, dict) or len(fields) > 128:
            return ("bpmn_input_projection_fields_invalid",)
        for alias, path in fields.items():
            if not isinstance(alias, str) or not _NAME.fullmatch(alias) or alias in _RESERVED:
                return ("bpmn_input_projection_alias_invalid",)
            if not isinstance(path, list) or not 2 <= len(path) <= 16:
                return ("bpmn_input_projection_path_invalid",)
            if any(not isinstance(key, str) or not key or len(key) > 128 or key in _RESERVED for key in path):
                return ("bpmn_input_projection_path_invalid",)
            if path[0] not in ({"results"} if group == "dependency_results" else {"input", "results"}):
                return ("bpmn_input_projection_root_invalid",)
    return ()


def projection_result_dependencies(value: dict) -> frozenset[str]:
    errors = validate_input_projection(value)
    if errors:
        raise ValueError(errors[0])
    return frozenset(
        path[1]
        for group in ("workflow_input", "dependency_results")
        for path in value[group].values()
        if path[0] == "results"
    )


def project_bpmn_inputs(value: dict, *, input_data: dict, results: dict) -> dict:
    """Return detached, bounded maps. Empty means empty, never ambient fallback."""
    errors = validate_input_projection(value)
    if errors:
        raise ValueError(errors[0])
    context = {"input": input_data, "results": results}
    output = {}
    for group in ("workflow_input", "dependency_results"):
        fields = {}
        for alias, path in value[group].items():
            selected = context
            for key in path:
                if not isinstance(selected, dict) or key not in selected:
                    raise ValueError("bpmn_input_projection_field_missing")
                selected = selected[key]
            fields[alias] = selected
        output[group] = fields
    _bounded_json(output)
    return json.loads(canonical_json(output))


def _bounded_json(value: dict) -> None:
    pending = [(value, 0)]
    remaining = 4096
    while pending:
        item, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise ValueError("bpmn_input_projection_value_limit")
        if isinstance(item, dict):
            if any(not isinstance(key, str) or key in _RESERVED for key in item):
                raise ValueError("bpmn_input_projection_value_invalid")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in {str, bool, int, float}:
            raise ValueError("bpmn_input_projection_value_invalid")
    if len(canonical_json(value).encode("utf-8")) > 65536:
        raise ValueError("bpmn_input_projection_value_limit")
