"""HDE-005: hub-direct routing inside TaskExecutionService.propose_direct_step."""
import pytest

from agent.models import TaskStepProposeRequest
from agent.services.task_execution_service import TaskExecutionService


class FakeLLM:
    def __init__(self, answer="LLM ANTWORT"):
        self.calls = []
        self.answer = answer

    def __call__(self, *, provider, model, prompt, urls, api_key, temperature=None, **kwargs):
        self.calls.append({"provider": provider, "model": model, "prompt": prompt})
        return self.answer


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    """Replace the execution plane with a fake and pin the workspace."""
    calls = []

    class _Runtime:
        runtime_kind = "fake"

        def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
            calls.append({"tool_name": tool_name, "workspace_dir": workspace_dir})
            return {
                "schema": "ananta_tool_result.v1",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "ok",
                "risk_class": "read",
                "evidence": [{"kind": "file_list", "excerpt": "a.py"}],
                "warnings": [],
            }

    from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
    from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter
    import agent.services.hub_tool_execution_adapter as adapter_module

    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(_Runtime()))
    monkeypatch.setattr(adapter_module, "hub_tool_execution_adapter", adapter)
    return calls


def _agent_cfg(tmp_path, **direct_overrides):
    direct = {
        "enabled": True,
        "direct_before_worker": True,
        "fallback_to_worker": True,
        "audit_enabled": False,
        "allowed_tools": ["repo.list_files", "git.status", "repo.grep"],
    }
    direct.update(direct_overrides)
    return {
        "provider": "ollama",
        "model": "llama3",
        "hub_direct_execution": direct,
        "worker_runtime": {"workspace_root": str(tmp_path)},
    }


def _propose(service, prompt, agent_cfg, llm):
    return service.propose_direct_step(
        TaskStepProposeRequest(prompt=prompt),
        agent_cfg=agent_cfg,
        provider_urls={},
        openai_api_key=None,
        agent_name="test-agent",
        llm_caller=llm,
    )


def test_simple_prompt_skips_llm_entirely(fake_runtime, tmp_path):
    llm = FakeLLM()
    response = _propose(TaskExecutionService(), "liste dateien", _agent_cfg(tmp_path), llm)
    assert llm.calls == [], "no llm_caller invocation for direct-eligible prompts"
    assert response["direct_execution"]["kind"] == "direct_tool_result"
    assert fake_runtime and fake_runtime[0]["tool_name"] == "repo.list_files"
    assert response["cost_summary"]["provider"] is None
    assert response["cost_summary"]["model"] is None
    assert response["cost_summary"]["tokens_total"] == 0
    assert "llm_call_profile" not in response


def test_non_eligible_prompt_falls_back_to_llm(fake_runtime, tmp_path):
    llm = FakeLLM()
    response = _propose(TaskExecutionService(), "implementiere bitte ein feature", _agent_cfg(tmp_path), llm)
    assert len(llm.calls) == 1
    assert "direct_execution" not in response
    assert fake_runtime == []


def test_disabled_feature_keeps_existing_behavior(fake_runtime, tmp_path):
    llm = FakeLLM()
    response = _propose(TaskExecutionService(), "liste dateien", _agent_cfg(tmp_path, enabled=False), llm)
    assert len(llm.calls) == 1
    assert "direct_execution" not in response


def test_missing_workspace_falls_back_to_worker(fake_runtime, tmp_path):
    llm = FakeLLM()
    cfg = _agent_cfg(tmp_path)
    cfg["worker_runtime"] = {}
    response = _propose(TaskExecutionService(), "liste dateien", cfg, llm)
    assert len(llm.calls) == 1
    assert fake_runtime == []


def test_direct_response_is_propose_response_compatible(fake_runtime, tmp_path):
    response = _propose(TaskExecutionService(), "git status", _agent_cfg(tmp_path), FakeLLM())
    for key in ("reason", "command", "tool_calls", "raw"):
        assert key in response
    assert response["command"] is None
    assert response["reason"].startswith("hub_direct_execution:")


def test_execute_direct_decision_runs_without_command_string(fake_runtime, tmp_path):
    from agent.services.hub_direct_execution_router import HubDirectExecutionRouter

    cfg = _agent_cfg(tmp_path)
    decision = HubDirectExecutionRouter().classify("git status", agent_cfg=cfg)
    assert decision.eligible
    result = TaskExecutionService().execute_direct_decision(decision, agent_cfg=cfg)
    assert result["kind"] == "direct_tool_result"
    assert fake_runtime[0]["tool_name"] == "git.status"


def test_existing_execute_direct_step_path_unchanged(tmp_path):
    """Backward compatibility: command-based execution still works."""
    from agent.models import TaskStepExecuteRequest

    service = TaskExecutionService()
    response = service.execute_direct_step(
        TaskStepExecuteRequest(command="echo hub-direct-kompat"),
        agent_cfg={},
        agent_name="test-agent",
    )
    assert "hub-direct-kompat" in str(response.get("output") or "")
    assert response.get("exit_code") == 0
