from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.mcp_readonly_adapter import MCPReadonlyAdapter
from agent.services.mcp_tool_registry import MCPToolRegistry

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "mcp" / "mcp_tool_descriptor.v1.json"


def _registry_with_read_tool() -> MCPToolRegistry:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    registry.register(
        {
            "tool_id": "tasks.get",
            "tool_name": "tasks.get",
            "capability": "mcp.tasks.read",
            "risk_class": "low",
            "access_class": "read",
            "allowed_scopes": ["tasks"],
            "default_enabled": False,
            "lifecycle": "enabled",
        }
    )
    return registry


def test_mcp_readonly_adapter_requires_capability_and_policy_gates() -> None:
    registry = _registry_with_read_tool()
    adapter = MCPReadonlyAdapter(
        registry=registry,
        dispatcher=lambda _tool, _args: {"ok": True},
        capability_gate=lambda _capability: False,
        policy_gate=lambda _policy: True,
    )
    with pytest.raises(PermissionError, match="capability_denied"):
        adapter.execute(tool_id="tasks.get", arguments={"task_id": "t1"})


def test_mcp_readonly_adapter_returns_provenance_rich_artifact() -> None:
    registry = _registry_with_read_tool()
    adapter = MCPReadonlyAdapter(
        registry=registry,
        dispatcher=lambda tool, args: {"tool": tool, "args": args, "task": {"id": "t1"}},
        capability_gate=lambda _capability: True,
        policy_gate=lambda _policy: True,
    )
    payload = adapter.execute(tool_id="tasks.get", arguments={"task_id": "t1"}, trace_id="trace-1")
    assert payload["status"] == "ok"
    assert payload["content"]["task"]["id"] == "t1"
    artifact = payload["artifact"]
    assert artifact["type"] == "mcp_readonly_result"
    assert artifact["provenance"]["adapter"] == "mcp_readonly_adapter"
    assert artifact["provenance"]["tool_class"] == "read"
    assert artifact["trace_id"] == "trace-1"


def test_mcp_readonly_adapter_blocks_write_admin_execution() -> None:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    registry.register(
        {
            "tool_id": "tasks.update",
            "tool_name": "tasks.update",
            "capability": "mcp.tasks.write",
            "risk_class": "high",
            "access_class": "write",
            "allowed_scopes": ["tasks"],
            "default_enabled": False,
            "lifecycle": "enabled",
        }
    )
    adapter = MCPReadonlyAdapter(
        registry=registry,
        dispatcher=lambda _tool, _args: {"ok": True},
        capability_gate=lambda _capability: True,
        policy_gate=lambda _policy: True,
    )
    with pytest.raises(PermissionError, match="non_read_tool_forbidden"):
        adapter.execute(tool_id="tasks.update", arguments={"task_id": "t1"})

