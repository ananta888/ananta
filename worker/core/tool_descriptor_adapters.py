"""Adapters from existing native, LangChain and MCP catalogs to one port."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from worker.core.tool_registry import (
    ToolInvocationEnvelope,
    WorkerToolEntry,
    WorkerToolRegistry,
)


class ToolCatalogSource(Protocol):
    def entries(self) -> Iterable[WorkerToolEntry]: ...


class AdaptedToolDescriptorRegistry:
    """Owns only an immutable adapter projection, never a process-global catalog."""

    def __init__(self, sources: Iterable[ToolCatalogSource]) -> None:
        self._registry = WorkerToolRegistry()
        for source in sources:
            for entry in source.entries():
                if self._registry.get(entry.id) is not None:
                    raise ValueError(f"tool_descriptor_collision:{entry.id}")
                self._registry.register(entry)

    def get(self, tool_id: str) -> WorkerToolEntry | None:
        return self._registry.get(tool_id)

    def validate_invocation(self, envelope: ToolInvocationEnvelope) -> list[str]:
        return self._registry.validate_invocation(envelope)

    def capability_catalog(self) -> list[dict[str, Any]]:
        return self._registry.capability_catalog()


class NativeAnantaToolCatalogSource:
    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def entries(self) -> Iterable[WorkerToolEntry]:
        for spec in self._registry.list_tools():
            category = str(spec.category)
            if category == "blocked":
                continue
            requirements = dict(spec.policy_requirements or {})
            capabilities = [f"tool:{category}", f"execution_plane:{spec.execution_plane}"]
            if requirements.get("requires_approval"):
                capabilities.append("approval:required")
            side_effects = () if category == "read_only" else ("native_tool_write",)
            if category == "controlled_execution":
                # Execution can have effects beyond the workspace; classify it
                # conservatively so the Hub descriptor cannot be downgraded.
                side_effects = ("persistent_state",)
            yield WorkerToolEntry(
                id=str(spec.name),
                kind="native",
                capability_classes=tuple(capabilities),
                risk_class=_native_risk(str(spec.risk_class)),
                input_schema=_closed_schema(dict(spec.argument_schema or {})),
                side_effects=side_effects,
                description=str(spec.description),
            )


class LangChainBuiltinToolCatalogSource:
    def entries(self) -> Iterable[WorkerToolEntry]:
        yield WorkerToolEntry(
            id="search_code",
            kind="langchain",
            capability_classes=("code_read", "codecompass_read"),
            risk_class="low",
            input_schema=_closed_schema(
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                }
            ),
            description="Search code through the common retrieval/tool gate.",
        )
        yield WorkerToolEntry(
            id="summarize_doc",
            kind="langchain",
            capability_classes=("text_generation",),
            risk_class="low",
            input_schema=_closed_schema(
                {
                    "type": "object",
                    "properties": {"text": {"type": "string", "minLength": 1}},
                    "required": ["text"],
                }
            ),
            description="Summarize text through the common provider/tool gate.",
        )


class MCPToolCatalogSource:
    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def entries(self) -> Iterable[WorkerToolEntry]:
        for descriptor in self._registry.list_descriptors():
            if str(descriptor.get("lifecycle") or "enabled") not in {"enabled", "degraded"}:
                continue
            access_class = str(descriptor.get("access_class") or "read")
            metadata = descriptor.get("metadata") if isinstance(descriptor.get("metadata"), dict) else {}
            schema = descriptor.get("input_schema") or metadata.get("input_schema") or {
                "type": "object",
                "properties": {},
            }
            side_effects = () if access_class == "read" else (f"mcp_{access_class}",)
            yield WorkerToolEntry(
                id=str(descriptor.get("tool_id") or ""),
                kind="mcp",
                capability_classes=tuple(
                    dict.fromkeys(
                        [
                            str(descriptor.get("capability") or "mcp"),
                            *(str(item) for item in descriptor.get("allowed_scopes") or ()),
                        ]
                    )
                ),
                risk_class=str(descriptor.get("risk_class") or "high"),
                input_schema=_closed_schema(dict(schema)),
                side_effects=side_effects,
                description=str(descriptor.get("description") or descriptor.get("tool_name") or ""),
            )


def _closed_schema(raw: dict[str, Any]) -> dict[str, Any]:
    schema = dict(raw)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("additionalProperties", False)
    return schema


def _native_risk(value: str) -> str:
    return {
        "read": "low",
        "execution": "medium",
        "write": "high",
        "admin": "critical",
        "external_agent": "critical",
    }.get(value, "high")


__all__ = [
    "AdaptedToolDescriptorRegistry",
    "LangChainBuiltinToolCatalogSource",
    "MCPToolCatalogSource",
    "NativeAnantaToolCatalogSource",
    "ToolCatalogSource",
]
