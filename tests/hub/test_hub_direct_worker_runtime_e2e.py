"""HDW-005: hub decides and dispatches; WorkerRuntime executes."""
from __future__ import annotations

from agent.models import TaskStepProposeRequest
from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
from agent.services.task_execution_service import TaskExecutionService
from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class RecordingRuntime:
    runtime_kind = "e2e_fake"

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
            "status": "ok",
            "risk_class": "read",
            "evidence": [{"kind": "file_list", "excerpt": "hello.py"}],
            "warnings": [],
        }


def test_hub_direct_lists_files_without_llm_and_dispatches_to_runtime(tmp_path, monkeypatch):
    runtime = RecordingRuntime()
    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(runtime))
    import agent.services.hub_tool_execution_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "hub_tool_execution_adapter", adapter)
    llm_calls = []

    def _llm(**kwargs):
        llm_calls.append(kwargs)
        return "should not be called"

    response = TaskExecutionService().propose_direct_step(
        TaskStepProposeRequest(prompt="liste dateien"),
        agent_cfg={
            "provider": "ollama",
            "model": "llama3",
            "hub_direct_execution": {
                "enabled": True,
                "direct_before_worker": True,
                "fallback_to_worker": True,
                "audit_enabled": False,
                "allowed_tools": ["repo.list_files"],
            },
            "worker_runtime": {"workspace_root": str(tmp_path)},
        },
        provider_urls={},
        openai_api_key=None,
        agent_name="test-agent",
        llm_caller=_llm,
    )

    assert llm_calls == []
    assert response["direct_execution"]["kind"] == "direct_tool_result"
    assert runtime.calls[0]["tool_name"] == "repo.list_files"
    assert runtime.calls[0]["workspace_dir"] == str(tmp_path.resolve())
    assert response["cost_summary"]["tokens_total"] == 0

