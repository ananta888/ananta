"""HDE-020: reuse-before-LLM — direct tools first, no silent policy bypass."""
import pytest

from agent.models import TaskStepProposeRequest
from agent.services.task_execution_service import TaskExecutionService


class FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, *, provider, model, prompt, urls, api_key, temperature=None, **kwargs):
        self.calls.append(prompt)
        return "LLM"


class ScriptedRuntime:
    runtime_kind = "scripted"

    def __init__(self, status="ok", error=None):
        self.status = status
        self.error = error
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append(tool_name)
        result = {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": self.status,
            "risk_class": "read",
            "evidence": [],
            "warnings": [],
        }
        if self.error:
            result["error"] = self.error
        return result


@pytest.fixture
def install_runtime(monkeypatch):
    def _install(runtime):
        from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
        from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter
        import agent.services.hub_tool_execution_adapter as adapter_module

        adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(runtime))
        monkeypatch.setattr(adapter_module, "hub_tool_execution_adapter", adapter)

    return _install


def _cfg(tmp_path, allowed=("git.status", "repo.grep", "repo.list_files")):
    return {
        "provider": "ollama",
        "model": "llama3",
        "hub_direct_execution": {
            "enabled": True,
            "direct_before_worker": True,
            "fallback_to_worker": True,
            "audit_enabled": False,
            "allowed_tools": list(allowed),
        },
        "worker_runtime": {"workspace_root": str(tmp_path)},
    }


def _propose(prompt, cfg, llm):
    return TaskExecutionService().propose_direct_step(
        TaskStepProposeRequest(prompt=prompt),
        agent_cfg=cfg,
        provider_urls={},
        openai_api_key=None,
        agent_name="test-agent",
        llm_caller=llm,
    )


def test_git_status_does_not_call_llm(install_runtime, tmp_path):
    runtime = ScriptedRuntime()
    install_runtime(runtime)
    llm = FakeLLM()
    response = _propose("git status", _cfg(tmp_path), llm)
    assert llm.calls == []
    assert runtime.calls == ["git.status"]
    cost = response["cost_summary"]
    assert cost["provider"] is None and cost["model"] is None
    assert cost["cost_units"] == 0.0


def test_architecture_analysis_calls_llm(install_runtime, tmp_path):
    runtime = ScriptedRuntime()
    install_runtime(runtime)
    llm = FakeLLM()
    _propose("analysiere Architektur und schlage Refactor vor", _cfg(tmp_path), llm)
    assert len(llm.calls) == 1
    assert runtime.calls == []


def test_recoverable_direct_failure_falls_back_to_worker(install_runtime, tmp_path):
    runtime = ScriptedRuntime(status="error", error="tool_execution_failed:io")
    install_runtime(runtime)
    llm = FakeLLM()
    response = _propose("git status", _cfg(tmp_path), llm)
    assert len(llm.calls) == 1, "recoverable tool failure -> worker fallback"
    assert "direct_execution" not in response


def test_policy_block_does_not_silently_fall_back_to_llm(install_runtime, tmp_path):
    runtime = ScriptedRuntime()
    install_runtime(runtime)
    llm = FakeLLM()
    # git.status matched by router but not in allowed_tools of the policy
    # gate: router rejects it (tool_not_in_allowed_tools) and the worker
    # may take over. A *policy block* however must not be bypassed:
    cfg = _cfg(tmp_path)
    cfg["hub_direct_execution"]["allowed_tools"] = []  # router stage passes everything

    from agent.services.hub_direct_execution_router import HubDirectExecutionRouter

    decision = HubDirectExecutionRouter().classify("git status", agent_cfg=cfg)
    assert decision.eligible

    # Force the policy gate to block by patching evaluate.
    from agent.services.ananta_tool_policy_service import ToolPolicyDecision
    import agent.services.hub_tool_execution_adapter as adapter_module

    class _BlockingPolicy:
        def evaluate(self, **kwargs):
            return ToolPolicyDecision(
                decision="policy_blocked", reason="test_block", rule_id="test", tool_name="git.status"
            )

    from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
    from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter

    adapter = HubToolExecutionAdapter(
        runtime_adapter=WorkerRuntimeExecutionAdapter(runtime), policy_service=_BlockingPolicy()
    )
    adapter_module.hub_tool_execution_adapter = adapter
    try:
        response = _propose("git status", cfg, llm)
    finally:
        adapter_module.hub_tool_execution_adapter = HubToolExecutionAdapter()

    assert llm.calls == [], "policy block must not be bypassed by an LLM fallback"
    assert response["direct_execution"]["kind"] == "direct_policy_blocked"
    assert runtime.calls == []
