"""UTCR-002: Thin adapter that delegates tool-schema concerns to
AnantaToolRegistryService.

Three public methods cover the three use-cases:
- prompt text for the LLM system prompt (``get_prompt_description``)
- native OpenAI tools list for the ``tools`` parameter (``get_openai_tools``)
- a stripped debug snapshot safe to log or expose to operators
  (``get_debug_snapshot``)

Module-level singleton + ``get_tool_schema_adapter()`` getter follow the
pattern used by every other ananta service.
"""
from __future__ import annotations

from typing import Any

from agent.services.ananta_tool_registry_service import (
    CATEGORY_BLOCKED,
    get_ananta_tool_registry_service,
)


class ToolSchemaAdapterService:
    """Thin adapter; all heavy logic lives in AnantaToolRegistryService."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_prompt_description(
        self,
        allowed_tools: list[str] | None = None,
        *,
        include_dynamic: bool = False,
    ) -> str:
        """Return the compact tool description string for the worker prompt."""
        return get_ananta_tool_registry_service().describe_for_prompt(
            allowed_tools, include_dynamic=include_dynamic
        )

    def get_openai_tools(
        self,
        allowed_tools: list[str] | None = None,
        *,
        include_dynamic: bool = False,
    ) -> list[dict[str, Any]]:
        """Return the native OpenAI-tools list (type/function envelope)."""
        return get_ananta_tool_registry_service().describe_for_openai_tools(
            allowed_tools, include_dynamic=include_dynamic
        )

    def get_debug_snapshot(
        self,
        allowed_tools: list[str] | None = None,
        *,
        include_dynamic: bool = False,
    ) -> dict[str, Any]:
        """Return a stripped snapshot safe for logging / operator inspection.

        Each tool entry contains: name, category, risk_class,
        execution_plane, source and a sorted list of argument property
        names. No implementation details, no secrets.
        """
        registry = get_ananta_tool_registry_service()
        allowed = {str(item or "").strip() for item in (allowed_tools or []) if str(item or "").strip()}
        tools: list[dict[str, Any]] = []

        for spec in registry.list_tools():
            if spec.category == CATEGORY_BLOCKED:
                continue
            if allowed and spec.name not in allowed:
                continue
            props = sorted((spec.argument_schema.get("properties") or {}).keys())
            tools.append(
                {
                    "name": spec.name,
                    "category": spec.category,
                    "risk_class": spec.risk_class,
                    "execution_plane": spec.execution_plane,
                    "source": "static",
                    "argument_properties": props,
                }
            )

        if include_dynamic:
            seen = {t["name"] for t in tools}
            for row in registry._dynamic_tool_rows():
                name = str(row.get("name") or "")
                if not name or name in seen:
                    continue
                if allowed and name not in allowed:
                    continue
                seen.add(name)
                props = sorted((row.get("argument_schema", {}).get("properties") or {}).keys())
                tools.append(
                    {
                        "name": name,
                        "category": row.get("category"),
                        "risk_class": row.get("risk_class"),
                        "execution_plane": row.get("execution_plane"),
                        "source": "dynamic",
                        "argument_properties": props,
                    }
                )

        return {
            "schema": "ananta_tool_schema_debug.v1",
            "tools": tools,
        }


_tool_schema_adapter_service = ToolSchemaAdapterService()


def get_tool_schema_adapter() -> ToolSchemaAdapterService:
    return _tool_schema_adapter_service
