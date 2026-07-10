from __future__ import annotations

from unittest.mock import patch

from agent.tools_registry import ToolRegistry
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def test_tool_registry_preserves_namespaced_tool_names():
    registry = ToolRegistry()

    @registry.register("git.status", "status", {"type": "object", "properties": {}})
    def _tool():
        return {"ok": True}

    result = registry.execute("git.status", {})

    assert result.success is True
    assert result.output == {"ok": True}


def test_tui_ops_command_uses_backend_contract_not_local_shell():
    state = OperatorState(endpoint="http://hub.local", audit_context={"token": "t"})
    snapshot = {
        "traffic_lights": {"git_dirty": True, "docker_engine": "red", "compose_health": "green"},
        "git": {"data": {"dirty": True}},
        "docker": {"data": {"available": False}},
        "compose": {"data": {"count": 1}},
    }

    with patch("client_surfaces.operator_tui.commands_ops.OpsApiClient") as client_cls:
        client_cls.return_value.snapshot.return_value = snapshot
        result = execute_command(":ops status", state)

    assert result.handled is True
    assert result.state.section_id == "ops"
    assert result.state.section_payloads["ops"] == snapshot
    assert "docker=red" in result.state.status_message
