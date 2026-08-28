from __future__ import annotations

from agent.services.ananta_tool_registry_service import AnantaToolRegistryService
from agent.services.mcp_tool_registry import MCPToolRegistry
from worker.core.tool_descriptor_adapters import (
    AdaptedToolDescriptorRegistry,
    LangChainBuiltinToolCatalogSource,
    MCPToolCatalogSource,
    NativeAnantaToolCatalogSource,
)
from worker.core.tool_registry import ToolInvocationEnvelope


def test_native_langchain_and_mcp_adapters_share_the_descriptor_contract() -> None:
    mcp = MCPToolRegistry()
    mcp.register(
        {
            "tool_id": "mcp.read",
            "tool_name": "mcp.read",
            "description": "Read an MCP resource.",
            "capability": "mcp_read",
            "risk_class": "low",
            "access_class": "read",
            "allowed_scopes": ["tenant_read"],
            "metadata": {
                "input_schema": {
                    "type": "object",
                    "properties": {"uri": {"type": "string"}},
                    "required": ["uri"],
                }
            },
        }
    )
    native = AdaptedToolDescriptorRegistry(
        [NativeAnantaToolCatalogSource(AnantaToolRegistryService())]
    )
    langchain = AdaptedToolDescriptorRegistry([LangChainBuiltinToolCatalogSource()])
    mcp_registry = AdaptedToolDescriptorRegistry([MCPToolCatalogSource(mcp)])

    for registry, tool_id, arguments in (
        (native, "repo.list_files", {"path_glob": "agent/**"}),
        (langchain, "search_code", {"query": "RetryBudgetOwner"}),
        (mcp_registry, "mcp.read", {"uri": "ananta://system/health"}),
    ):
        descriptor = registry.get(tool_id)
        assert descriptor is not None
        assert descriptor.policy_scopes
        assert descriptor.side_effect_class == "read"
        assert registry.validate_invocation(
            ToolInvocationEnvelope(
                execution_id="attempt-1",
                tool_id=tool_id,
                arguments=arguments,
            )
        ) == []


def test_every_adapter_rejects_argument_schema_evasion_and_extra_fields() -> None:
    registries = (
        (
            AdaptedToolDescriptorRegistry(
                [NativeAnantaToolCatalogSource(AnantaToolRegistryService())]
            ),
            "repo.list_files",
        ),
        (
            AdaptedToolDescriptorRegistry([LangChainBuiltinToolCatalogSource()]),
            "search_code",
        ),
    )

    for registry, tool_id in registries:
        errors = registry.validate_invocation(
            ToolInvocationEnvelope(
                execution_id="attempt-1",
                tool_id=tool_id,
                arguments={"__proto__": {"scope": "admin"}},
            )
        )
        assert errors
        assert any("additional" in error.lower() or "required" in error.lower() for error in errors)
