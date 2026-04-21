from __future__ import annotations

from typing import Any

from agent.tool_capabilities import ToolCapability, build_capability_contract


TOOL_CONTRACT_VERSION = "v1"

TOOL_CONTRACT_SCHEMA: dict[str, Any] = {
    "version": TOOL_CONTRACT_VERSION,
    "required_fields": [
        "name",
        "category",
        "requires_admin",
        "mutates_state",
        "description",
        "input_contract",
        "output_contract",
        "audit",
        "security",
    ],
    "categories": ["read", "write", "admin"],
    "security_rules": [
        "Unknown tools fail closed.",
        "Mutating tools require admin unless an explicit policy says otherwise.",
        "Tools are allowlist/denylist controlled before execution.",
        "Hub owns validation, audit and orchestration; tools do not delegate work to workers directly.",
    ],
}


def _tool_contract(tool: ToolCapability) -> dict[str, Any]:
    return {
        "name": tool.tool,
        "category": tool.category,
        "requires_admin": tool.requires_admin,
        "mutates_state": tool.mutates_state,
        "description": tool.description,
        "input_contract": {
            "type": "object",
            "required": ["name"],
            "additional_properties": True,
            "notes": "Tool-specific args are validated by the owning route/service before side effects.",
        },
        "output_contract": {
            "envelope": "api_response or tool result object",
            "must_include": ["status"],
            "error_shape": "api_response(status='error', message=..., data.error_help optional)",
        },
        "audit": {
            "required_for_mutation": tool.mutates_state,
            "recommended_action": f"tool_{tool.tool}",
        },
        "security": {
            "fail_closed": True,
            "admin_required": tool.requires_admin,
            "state_mutation": tool.mutates_state,
            "policy_gate": "assistant_tool_capabilities + llm_tool_allowlist/llm_tool_denylist",
        },
    }


def build_tool_contract_catalog(agent_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = build_capability_contract(agent_cfg)
    return {
        "version": TOOL_CONTRACT_VERSION,
        "schema": dict(TOOL_CONTRACT_SCHEMA),
        "tools": [_tool_contract(tool) for _, tool in sorted(contract.items())],
    }
