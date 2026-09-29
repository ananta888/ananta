"""HDE-006: registry bridge + control-plane dispatch tests."""
import pytest

from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter, derive_mutation_mode
from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class FakeWorkerRuntime:
    runtime_kind = "fake"

    def __init__(self):
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append({"tool_name": tool_name, "arguments": dict(arguments), "workspace_dir": workspace_dir})
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "ok",
            "risk_class": "read",
            "evidence": [{"kind": "grep_match", "excerpt": "treffer"}],
            "warnings": [],
        }


def _cfg(**overrides):
    direct = {
        "enabled": True,
        "audit_enabled": False,
        "allowed_tools": ["repo.grep", "repo.list_files", "git.status"],
        "max_result_chars": 8000,
    }
    direct.update(overrides)
    return {"hub_direct_execution": direct}


def _adapter(runtime=None):
    return HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(runtime or FakeWorkerRuntime()))


def test_repo_grep_returns_ananta_tool_result_via_adapter(tmp_path):
    runtime = FakeWorkerRuntime()
    result = _adapter(runtime).execute_direct(
        tool_name="repo.grep",
        arguments={"pattern": "x", "limit": 5},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_tool_result"
    assert result["tool_result"]["schema"] == "ananta_tool_result.v1"
    assert result["policy_decision"]["decision"] == "allow"
    assert runtime.calls[0]["tool_name"] == "repo.grep"


def test_hub_adapter_never_executes_locally_without_runtime(tmp_path, monkeypatch):
    """The control plane must not import/run the hub-side executor."""
    import agent.services.tools as tools_pkg

    def _forbidden(**kwargs):
        raise AssertionError("hub-side execute_ananta_tool must not be called by the control plane")

    monkeypatch.setattr(tools_pkg, "execute_ananta_tool", _forbidden)
    runtime = FakeWorkerRuntime()
    result = _adapter(runtime).execute_direct(
        tool_name="repo.grep",
        arguments={"pattern": "x"},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_tool_result"
    assert runtime.calls


def test_classic_tool_registry_stays_untouched(tmp_path, monkeypatch):
    """HDE-006: hub-direct must not call agent.tools.registry.execute."""
    from agent.tools import registry as classic_registry

    def _forbidden(*args, **kwargs):
        raise AssertionError("agent.tools.registry must not execute hub-direct calls")

    if hasattr(classic_registry, "execute"):
        monkeypatch.setattr(classic_registry, "execute", _forbidden)
    result = _adapter().execute_direct(
        tool_name="repo.list_files",
        arguments={"limit": 5},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_tool_result"


def test_derive_mutation_mode_uses_task_kind():
    agent_cfg = {
        "ananta_worker_workspace_mutation": {
            "mutation_mode": "read_only",
            "mode_by_task_kind": {"coding": "controlled_workspace"},
        }
    }
    assert derive_mutation_mode({"task_kind": "coding"}, agent_cfg) == "controlled_workspace"
    assert derive_mutation_mode({"task_kind": "analysis"}, agent_cfg) == "read_only"
    assert derive_mutation_mode(None, agent_cfg) == "read_only"
    assert derive_mutation_mode(None, {}) == "read_only"


def test_worker_runtime_bound_result_preserves_source_line_policy_data():
    result = {
        "schema": "ananta_tool_result.v1",
        "status": "ok",
        "evidence": [{"kind": "policy_result", "excerpt": "x" * 200}],
        "warnings": [],
        "data": {
            "source_line_policy_result": {
                "schema": "generated_source_line_policy_result.v1",
                "status": "blocked",
                "summary": {"blocked": 1},
            }
        },
    }

    bounded = WorkerRuntimeExecutionAdapter._bound_result(result, max_chars=20)

    assert bounded["data"]["source_line_policy_result"]["summary"]["blocked"] == 1
    assert bounded["warnings"] == ["evidence_truncated"]
