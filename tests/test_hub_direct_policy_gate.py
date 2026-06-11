"""HDE-007: the policy gate guards every hub-direct call."""
import pytest

from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class CountingRuntime:
    runtime_kind = "counting"

    def __init__(self):
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append(tool_name)
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "ok",
            "risk_class": "read",
            "evidence": [],
            "warnings": [],
        }


def _cfg(allowed_tools):
    return {
        "hub_direct_execution": {
            "enabled": True,
            "audit_enabled": False,
            "allowed_tools": allowed_tools,
        }
    }


@pytest.fixture
def runtime():
    return CountingRuntime()


@pytest.fixture
def adapter(runtime):
    return HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(runtime))


def test_unknown_tool_is_blocked_not_executed(adapter, runtime, tmp_path):
    result = adapter.execute_direct(
        tool_name="repo.does_not_exist", arguments={}, agent_cfg=_cfg(["repo.does_not_exist"]),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_policy_blocked"
    assert "unknown_tool" in result["policy_decision"]["reason"]
    assert runtime.calls == []


def test_blocked_category_tool_is_blocked(adapter, runtime, tmp_path):
    result = adapter.execute_direct(
        tool_name="shell.run_unrestricted", arguments={}, agent_cfg=_cfg(["shell.run_unrestricted"]),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_policy_blocked"
    assert result["policy_decision"]["rule_id"] == "blocked_category"
    assert runtime.calls == []


def test_write_tool_in_read_only_mode_is_blocked(adapter, runtime, tmp_path):
    result = adapter.execute_direct(
        tool_name="repo.write_file",
        arguments={"path": "a.txt", "content": "x", "mode": "create_only"},
        agent_cfg=_cfg(["repo.write_file"]),
        workspace_ref=str(tmp_path),
        mutation_mode="read_only",
    )
    assert result["kind"] == "direct_policy_blocked"
    assert result["policy_decision"]["rule_id"] == "mutation_mode_gate"
    assert runtime.calls == []


def test_tool_outside_allowed_tools_is_blocked(adapter, runtime, tmp_path):
    result = adapter.execute_direct(
        tool_name="git.status", arguments={}, agent_cfg=_cfg(["repo.grep"]),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_policy_blocked"
    assert result["policy_decision"]["rule_id"] == "allowed_tools_scope"
    assert runtime.calls == []


def test_allowed_read_only_tool_executes(adapter, runtime, tmp_path):
    result = adapter.execute_direct(
        tool_name="git.status", arguments={}, agent_cfg=_cfg(["git.status"]),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_tool_result"
    assert runtime.calls == ["git.status"]


def test_execution_plane_gate_blocks_specless_dynamic_tool(adapter, runtime, tmp_path, monkeypatch):
    """HDW-003: custom tools without a valid execution_plane never run."""

    class _Registry:
        def get_active_tool(self, name):
            return {
                "name": name,
                "status": "active",
                "approval_status": "granted",
                "spec": {"name": name, "category": "read_only", "risk_class": "read"},
            }

    import agent.services.dynamic_tool_registry_service as registry_module

    monkeypatch.setattr(registry_module, "get_dynamic_tool_registry_service", lambda: _Registry())
    result = adapter.execute_direct(
        tool_name="custom.no_plane", arguments={}, agent_cfg=_cfg(["custom.no_plane"]),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_policy_blocked"
    assert result["policy_decision"]["rule_id"] == "execution_plane_gate"
    assert runtime.calls == []
