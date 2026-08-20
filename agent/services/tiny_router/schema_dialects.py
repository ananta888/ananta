"""Loss-aware projections from Ananta's canonical OpenAI tool schemas."""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from agent.services.tiny_router.types import SchemaProjection


class ToolSchemaDialectAdapter:
    """Projects schemas only; it never obtains tools from another registry."""

    def project(
        self, tools: Sequence[Mapping[str, Any]], *, dialect: str,
    ) -> SchemaProjection:
        canonical = tuple(self._validate_canonical(item) for item in tools)
        normalized = str(dialect or "").strip().lower()
        if normalized in {"openai", "functiongemma"}:
            return SchemaProjection(normalized, canonical)
        if normalized == "needle":
            return SchemaProjection(
                normalized,
                tuple(copy.deepcopy(item["function"]) for item in canonical),
            )
        if normalized == "xlam":
            rows: list[dict[str, Any]] = []
            losses: list[str] = []
            for item in canonical:
                function = item["function"]
                parameters = function.get("parameters") or {}
                if parameters.get("required"):
                    losses.append("required_not_represented:" + str(function["name"]))
                if parameters.get("additionalProperties") is False:
                    losses.append(
                        "additional_properties_not_represented:" + str(function["name"])
                    )
                rows.append({
                    "name": function["name"],
                    "description": function.get("description") or "",
                    "parameters": copy.deepcopy(parameters.get("properties") or {}),
                })
            return SchemaProjection(normalized, tuple(rows), tuple(sorted(set(losses))))
        raise ValueError("unsupported_tool_schema_dialect:" + normalized)

    @staticmethod
    def _validate_canonical(item: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(item, Mapping) or item.get("type") != "function":
            raise ValueError("canonical_tool_envelope_required")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("canonical_tool_function_required")
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters")
        if not name or not isinstance(parameters, Mapping):
            raise ValueError("canonical_tool_name_and_parameters_required")
        return copy.deepcopy(dict(item))
