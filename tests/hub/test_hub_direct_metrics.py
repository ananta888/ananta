"""HDE-021: metrics for saved LLM calls and tool reuse."""
import pytest

from agent.services.task_execution_metrics import (
    hub_direct_metrics_snapshot,
    last_hub_direct_decisions,
    record_hub_direct_decision,
    record_hub_direct_metric,
    reset_hub_direct_metrics,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    reset_hub_direct_metrics()
    yield
    reset_hub_direct_metrics()


def test_counters_increment():
    record_hub_direct_metric("direct_execution_count", tool_name="repo.grep", reason_code="deterministic_rule_match")
    record_hub_direct_metric("direct_execution_success_count", tool_name="repo.grep")
    record_hub_direct_metric("avoided_llm_call_count", tool_name="repo.grep")
    snapshot = hub_direct_metrics_snapshot()
    assert snapshot["direct_execution_count"] == 1
    assert snapshot["direct_execution_success_count"] == 1
    assert snapshot["avoided_llm_call_count"] == 1
    assert snapshot["by_tool"]["repo.grep"] == 3
    assert snapshot["by_reason"]["deterministic_rule_match"] == 1


def test_blocked_and_fallback_counters():
    record_hub_direct_metric("direct_execution_blocked_count", tool_name="repo.write_file", reason_code="mutation_mode_gate")
    record_hub_direct_metric("fallback_to_worker_count", reason_code="no_rule_match")
    record_hub_direct_metric("custom_tool_reuse_count", tool_name="custom.count_todos")
    snapshot = hub_direct_metrics_snapshot()
    assert snapshot["direct_execution_blocked_count"] == 1
    assert snapshot["fallback_to_worker_count"] == 1
    assert snapshot["custom_tool_reuse_count"] == 1


def test_unknown_metric_is_ignored():
    record_hub_direct_metric("does_not_exist", tool_name="x")
    snapshot = hub_direct_metrics_snapshot()
    assert "does_not_exist" not in snapshot


def test_snapshot_contains_no_prompts_or_outputs():
    record_hub_direct_decision(
        {
            "tool_name": "repo.grep",
            "reason_code": "deterministic_rule_match",
            "kind": "direct_tool_result",
            "task_id": "t-1",
            "status": "ok",
            "source": "static",
            "prompt": "geheimer prompt",
            "output": "geheime ausgabe",
        }
    )
    decisions = last_hub_direct_decisions()
    assert decisions[0]["tool_name"] == "repo.grep"
    assert "prompt" not in decisions[0]
    assert "output" not in decisions[0]


def test_proposal_response_carries_zero_token_cost_summary(monkeypatch, tmp_path):
    """TaskExecutionService adds a zero-LLM cost_summary for direct runs."""
    from agent.models import TaskStepProposeRequest
    from agent.services.task_execution_service import TaskExecutionService
    import agent.services.hub_tool_execution_adapter as adapter_module
    from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
    from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter

    class _Runtime:
        runtime_kind = "fake"

        def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
            return {
                "schema": "ananta_tool_result.v1",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "ok",
                "risk_class": "read",
                "evidence": [],
                "warnings": [],
            }

    monkeypatch.setattr(
        adapter_module,
        "hub_tool_execution_adapter",
        HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(_Runtime())),
    )
    cfg = {
        "hub_direct_execution": {"enabled": True, "audit_enabled": False, "allowed_tools": ["git.status"]},
        "worker_runtime": {"workspace_root": str(tmp_path)},
    }
    response = TaskExecutionService().propose_direct_step(
        TaskStepProposeRequest(prompt="git status"),
        agent_cfg=cfg,
        provider_urls={},
        openai_api_key=None,
        agent_name="test-agent",
        llm_caller=lambda **kwargs: pytest.fail("llm must not be called"),
    )
    assert response["cost_summary"]["tokens_total"] == 0

    snapshot = hub_direct_metrics_snapshot()
    assert snapshot["direct_execution_count"] == 1
    assert snapshot["direct_execution_success_count"] == 1
    assert snapshot["avoided_llm_call_count"] == 1
