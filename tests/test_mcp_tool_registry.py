from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.mcp_tool_registry import MCPToolRegistry

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "mcp" / "mcp_tool_descriptor.v1.json"


def _descriptor(**overrides):
    payload = {
        "tool_id": "tasks.get",
        "tool_name": "tasks.get",
        "capability": "mcp.tasks.read",
        "risk_class": "low",
        "access_class": "read",
        "allowed_scopes": ["tasks"],
        "default_enabled": False,
        "lifecycle": "enabled",
    }
    payload.update(overrides)
    return payload


def test_mcp_tool_registry_registers_valid_descriptor() -> None:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    registry.register(_descriptor())
    descriptor = registry.get("tasks.get")
    assert descriptor is not None
    assert descriptor["access_class"] == "read"
    assert registry.is_tool_available("tasks.get", scope="tasks") is True
    assert registry.health()["status"] == "healthy"


def test_mcp_tool_registry_unknown_tool_is_unavailable_by_default() -> None:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    assert registry.is_tool_available("unknown.tool") is False


def test_mcp_tool_registry_rejects_duplicate_tool() -> None:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    registry.register(_descriptor())
    with pytest.raises(ValueError, match="duplicate_tool"):
        registry.register(_descriptor())


def test_mcp_tool_registry_rejects_unsafe_default_for_write_admin() -> None:
    registry = MCPToolRegistry(schema_path=SCHEMA_PATH)
    with pytest.raises(ValueError, match="unsafe_default_enabled"):
        registry.register(
            _descriptor(
                tool_id="tasks.update",
                tool_name="tasks.update",
                access_class="write",
                risk_class="high",
                default_enabled=True,
            )
        )

