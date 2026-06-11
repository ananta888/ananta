"""HDW-004: workspace and env boundaries of the execution plane."""
import os

from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class _RecordingRuntime:
    runtime_kind = "recording"

    def __init__(self):
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append({"workspace_dir": workspace_dir, "config": dict(config)})
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "success",
            "risk_class": "read",
            "evidence": [],
            "warnings": [],
        }


def _allow():
    return {"decision": "allow", "risk_class": "read"}


def test_missing_workspace_is_rejected_not_defaulted():
    """No implicit fallback to the hub working directory."""
    adapter = WorkerRuntimeExecutionAdapter(_RecordingRuntime())
    for ref in (None, "", "   "):
        result = adapter.dispatch(
            tool_name="repo.grep", arguments={}, workspace_ref=ref,
            policy_decision=_allow(), audit_enabled=False,
        )
        assert result["error"] == "missing_or_invalid_workspace_ref"


def test_nonexistent_workspace_is_rejected(tmp_path):
    adapter = WorkerRuntimeExecutionAdapter(_RecordingRuntime())
    result = adapter.dispatch(
        tool_name="repo.grep", arguments={}, workspace_ref=str(tmp_path / "missing"),
        policy_decision=_allow(), audit_enabled=False,
    )
    assert result["error"] == "missing_or_invalid_workspace_ref"


def test_path_traversal_is_normalized(tmp_path):
    runtime = _RecordingRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    nested = tmp_path / "ws"
    nested.mkdir()
    adapter.dispatch(
        tool_name="repo.grep", arguments={},
        workspace_ref=str(nested / ".." / "ws"),
        policy_decision=_allow(), audit_enabled=False,
    )
    assert runtime.calls[0]["workspace_dir"] == str(nested.resolve())


def test_env_passthrough_only_for_allowlisted_names(tmp_path, monkeypatch):
    monkeypatch.setenv("ANANTA_TEST_ALLOWED", "ja")
    monkeypatch.setenv("ANANTA_TEST_FORBIDDEN", "nein")
    runtime = _RecordingRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    adapter.dispatch(
        tool_name="repo.grep", arguments={}, workspace_ref=str(tmp_path),
        policy_decision=_allow(),
        config={"env_allowlist": ["ANANTA_TEST_ALLOWED"]},
        audit_enabled=False,
    )
    env = runtime.calls[0]["config"]["env"]
    assert env == {"ANANTA_TEST_ALLOWED": "ja"}
    assert "ANANTA_TEST_FORBIDDEN" not in env


def test_mutation_mode_is_forwarded_not_widened(tmp_path):
    runtime = _RecordingRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    adapter.dispatch(
        tool_name="repo.grep", arguments={}, workspace_ref=str(tmp_path),
        mutation_mode="read_only", policy_decision=_allow(), audit_enabled=False,
    )
    assert runtime.calls[0]["config"]["mutation_mode"] == "read_only"
