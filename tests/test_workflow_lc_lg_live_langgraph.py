"""Tests for the LangGraph live LLM-node invocation (LCG-008 v0.8 closure).

The graph walker now actually calls the LLM at llm-kind nodes.
The runner selection mirrors the LangChain adapter:

- SimplexRunner when the framework is missing.
- LangChainRunnableRunner when `langchain-core` is importable.

The contract: a graph with at least one llm node calls the runner,
records the response in state.llm_responses, and the audit log
gets a `node_llm_invoked` event. A pre-saturated budget surfaces
as a failed result via the execute() WorkerError path.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agent.providers.lc_lg import LangGraphProviderConfig
from worker.adapters.langgraph_adapter import LangGraphAdapter


# ── Adapter-level integration ─────────────────────────────────────────


def test_lg_adapter_live_path_uses_simplex_for_llm_node(monkeypatch):
    """Without langchain, the llm node uses SimplexRunner."""
    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5)
    a = LangGraphAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "draft", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "draft", "to": "end"}],
        }
    }
    with patch("agent.llm_integration.generate_text", return_value="llm-out"):
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "success"
    # The walker visited both nodes; the first was the llm node.
    assert r.artifacts[0]["nodes_visited"] == ["draft", "end"]


def test_lg_adapter_live_path_uses_langchain_runnable_for_llm_node(monkeypatch):
    """With langchain installed, the llm node uses LangChainRunnableRunner.

    We monkey-patch RunnableLambda to a fake that just calls the
    inner function, so the test never opens a real LLM connection.
    """
    pytest.importorskip("langchain_core.runnables",
                        reason="langchain-core not installed (pip install ananta[langchain])")
    import langchain_core.runnables as rc  # type: ignore

    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(rc, "RunnableLambda",
                        staticmethod(lambda fn: type("R", (), {
                            "invoke": staticmethod(lambda x: fn(x))
                        })()))

    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5)
    a = LangGraphAdapter(cfg)
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "draft", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "draft", "to": "end"}],
        }
    }
    with patch("agent.llm_integration.generate_text", return_value="ok"):
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "success"
    # The audit trace should mention the langchain_runnable runner.
    audit_event = next(
        (e for e in r.execution_trace if e.get("event") == "node_llm_invoked"),
        None,
    )
    assert audit_event is not None
    assert audit_event["runner"] == "langchain_runnable"
    assert audit_event["node"] == "draft"


def test_lg_adapter_llm_node_records_response_in_state(monkeypatch):
    """state.llm_responses gets one entry per llm node visited."""
    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5)
    a = LangGraphAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "a", "kind": "llm"},
                {"id": "b", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "end"},
            ],
        }
    }
    with patch("agent.llm_integration.generate_text",
               side_effect=["out-a", "out-b"]):
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "success"
    # The artifact should reflect that both nodes were visited.
    assert r.artifacts[0]["nodes_visited"] == ["a", "b", "end"]


def test_lg_adapter_human_gate_requires_approval(monkeypatch):
    """A human_gate node forces approval_required in dry_run; execute() blocks.

    This is the policy-layer behaviour, not a regression. The human
    gate is meant to pause the run; the walker itself is only
    reached after the human approves.
    """
    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5)
    a = LangGraphAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "gate", "kind": "human_gate"},
                {"id": "draft", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [
                {"from": "gate", "to": "draft"},
                {"from": "draft", "to": "end"},
            ],
        }
    }
    with patch("agent.llm_integration.generate_text", return_value="nope") as m:
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "blocked"
    assert r.reason_code == "human_approval_required"
    # The walker stopped at the gate, so generate_text was never called.
    m.assert_not_called()


def test_lg_adapter_no_human_gate_proceeds(monkeypatch):
    """Without a human_gate, execute() runs through the LLM node and succeeds."""
    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5)
    a = LangGraphAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "router", "kind": "router"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "router", "to": "end"}],
        }
    }
    with patch("agent.llm_integration.generate_text", return_value="nope") as m:
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "success"
    m.assert_not_called()


def test_lg_adapter_llm_node_budget_failure_returns_failed(monkeypatch):
    """A pre-saturated budget surfaces as a failed result."""
    cfg = LangGraphProviderConfig(enabled=True, mode="local_live",
                                  max_iterations=5, max_nodes=5,
                                  timeout_seconds=300)
    # Use a small max_steps so we can saturate it cheaply.
    cfg_for_run = LangGraphProviderConfig(enabled=True, mode="local_live",
                                          max_iterations=5, max_nodes=5,
                                          timeout_seconds=300,
                                          metadata={"max_steps_override": 1})
    a = LangGraphAdapter(cfg_for_run)
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )

    # Pre-saturate the budget by monkey-patching the constructor.
    from worker.adapters import workflow_budget as wb_mod
    original_init = wb_mod.WorkflowBudgetGuard.__init__

    def saturated_init(self, **kwargs):
        kwargs.setdefault("max_steps", 10)
        kwargs.setdefault("timeout_seconds", 60)
        original_init(self, **kwargs)
        # Pre-consume all steps so the next record_step raises.
        self._steps = self._max_steps

    monkeypatch.setattr(wb_mod.WorkflowBudgetGuard, "__init__", saturated_init)

    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "draft", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "draft", "to": "end"}],
        }
    }
    with patch("agent.llm_integration.generate_text", return_value="ok"):
        r = a.execute(task_id="t1", task_type="agent_workflow", payload=payload)
    assert r.status == "failed"
    assert r.reason_code == "budget_steps_exceeded"
