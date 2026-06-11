"""HDW-002: the hub only dispatches — execution happens in the runtime."""
import pytest

from agent.services.worker_runtime_execution_adapter import (
    LocalProcessWorkerRuntime,
    WorkerRuntimeExecutionAdapter,
)


class FakeWorkerRuntime:
    runtime_kind = "fake"

    def __init__(self):
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": dict(arguments),
                "workspace_dir": workspace_dir,
                "tool_call_id": tool_call_id,
                "config": dict(config),
            }
        )
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "success",
            "risk_class": "read",
            "evidence": [{"kind": "file_list", "excerpt": "a.py"}],
            "warnings": [],
        }


def _allow_decision():
    return {"decision": "allow", "risk_class": "read"}


def test_dispatch_delegates_to_runtime_not_hub(tmp_path):
    runtime = FakeWorkerRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    result = adapter.dispatch(
        tool_name="repo.list_files",
        arguments={"limit": 5},
        workspace_ref=str(tmp_path),
        policy_decision=_allow_decision(),
        audit_enabled=False,
    )
    assert result["status"] == "success"
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["tool_name"] == "repo.list_files"
    assert runtime.calls[0]["workspace_dir"] == str(tmp_path.resolve())


def test_dispatch_refuses_without_allow_decision(tmp_path):
    runtime = FakeWorkerRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    result = adapter.dispatch(
        tool_name="repo.list_files",
        arguments={},
        workspace_ref=str(tmp_path),
        policy_decision={"decision": "policy_blocked"},
        audit_enabled=False,
    )
    assert result["status"] == "error"
    assert result["error"] == "dispatch_without_allow_decision"
    assert runtime.calls == []


def test_dispatch_requires_explicit_workspace():
    runtime = FakeWorkerRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    result = adapter.dispatch(
        tool_name="repo.list_files",
        arguments={},
        workspace_ref=None,
        policy_decision=_allow_decision(),
        audit_enabled=False,
    )
    assert result["status"] == "error"
    assert result["error"] == "missing_or_invalid_workspace_ref"
    assert runtime.calls == []


def test_dispatch_returns_runtime_failures_as_error(tmp_path):
    class _Boom:
        runtime_kind = "boom"

        def execute_tool(self, **kwargs):
            raise RuntimeError("kaputt")

    adapter = WorkerRuntimeExecutionAdapter(_Boom())
    result = adapter.dispatch(
        tool_name="repo.grep",
        arguments={},
        workspace_ref=str(tmp_path),
        policy_decision=_allow_decision(),
        audit_enabled=False,
    )
    assert result["status"] == "error"
    assert "worker_runtime_failed" in result["error"]


def test_result_is_bounded_to_max_result_chars(tmp_path):
    class _Chatty(FakeWorkerRuntime):
        def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
            return {
                "schema": "ananta_tool_result.v1",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "success",
                "risk_class": "read",
                "evidence": [{"kind": "file_excerpt", "excerpt": "x" * 10_000}],
                "warnings": [],
            }

    adapter = WorkerRuntimeExecutionAdapter(_Chatty())
    result = adapter.dispatch(
        tool_name="repo.read_file_range",
        arguments={},
        workspace_ref=str(tmp_path),
        policy_decision=_allow_decision(),
        config={"max_result_chars": 500},
        audit_enabled=False,
    )
    assert "evidence_truncated" in result["warnings"]
    assert len(result["evidence"][0]["excerpt"]) <= 520


def test_sensitive_config_keys_never_reach_runtime(tmp_path):
    runtime = FakeWorkerRuntime()
    adapter = WorkerRuntimeExecutionAdapter(runtime)
    adapter.dispatch(
        tool_name="repo.list_files",
        arguments={},
        workspace_ref=str(tmp_path),
        policy_decision=_allow_decision(),
        config={"openai_api_key": "geheim", "agent_token": "geheim", "max_result_chars": 100},
        audit_enabled=False,
    )
    runtime_cfg = runtime.calls[0]["config"]
    assert "openai_api_key" not in runtime_cfg
    assert "agent_token" not in runtime_cfg


def test_local_process_runtime_executes_static_tool(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    result = LocalProcessWorkerRuntime().execute_tool(
        tool_name="repo.list_files",
        arguments={"limit": 10},
        workspace_dir=str(tmp_path),
        tool_call_id="t-1",
        config={},
    )
    assert result["schema"] == "ananta_tool_result.v1"
    assert result["status"] == "ok"
