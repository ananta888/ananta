"""End-to-end smoke tests for LangChain/LangGraph adapters (LCG-029).

These tests run WITHOUT the LangChain/LangGraph frameworks installed —
they prove the contract works with default-off and dry_run config.
The tests for the live path are in the optional-extras e2e suite
(only collected when ananta[langchain] / ananta[langgraph] is
installed).
"""
from __future__ import annotations

import pytest

from agent.providers.lc_lg import (
    LangChainProviderConfig,
    LangGraphProviderConfig,
)
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.workflow_adapter_base import (
    DryRunResult,
    WorkflowArtifactResult,
)
from worker.adapters.workflow_adapter_registry import list_adapters_as_dicts


# ── Registry inclusion ─────────────────────────────────────────────────


def test_registry_lists_both_adapters():
    ids = {a["adapter_id"] for a in list_adapters_as_dicts()}
    assert "adapter.langchain" in ids
    assert "adapter.langgraph" in ids


def test_registry_adapters_disabled_by_default():
    """Default-off means every adapter reports disabled, never enabled."""
    for a in list_adapters_as_dicts():
        if a["adapter_id"] in {"adapter.langchain", "adapter.langgraph"}:
            assert a["status"] in {"disabled", "degraded"}, a
            assert a["enabled"] is False, a


# ── LangChain smoke ────────────────────────────────────────────────────


def test_lc_descriptor_default_off():
    desc = LangChainAdapter().descriptor()
    assert desc.status == "disabled"
    assert desc.enabled is False
    assert "langchain_not_installed" in desc.reason or \
           "adapter_disabled_by_config" in desc.reason


def test_lc_dry_run_rag_query_returns_plan():
    a = LangChainAdapter()
    r = a.dry_run(task_id="t1", task_type="rag_query",
                  payload={"query": "What is the workflow adapter interface?"})
    assert isinstance(r, DryRunResult)
    assert r.adapter_id == "adapter.langchain"
    assert r.task_id == "t1"
    assert r.task_type == "rag_query"
    assert r.risk_level == "low"
    assert not r.blocked
    # Plan steps are present and ordered. The plan is a list of dicts
    # with an 'action' field; check the actions form the expected chain.
    actions = [s["action"] for s in r.plan_steps]
    assert "codecompass_query" in actions
    assert "langchain_rag_query" in actions
    assert "artifact_write" in actions


def test_lc_dry_run_unknown_task_type_blocked():
    a = LangChainAdapter()
    r = a.dry_run(task_id="t1", task_type="unsupported_type", payload={})
    assert r.blocked
    assert "unsupported_task_type" in r.block_reason


def test_lc_dry_run_external_url_blocked_by_default():
    """A chain that wants a non-codecompass source is blocked."""
    a = LangChainAdapter()
    r = a.dry_run(task_id="t1", task_type="rag_query",
                  payload={"query": "x", "external_url": "https://api.example.com/x"})
    assert r.blocked
    assert "external" in r.block_reason.lower()


def test_lc_execute_default_off_returns_blocked():
    """Live execute is blocked when provider is default-off."""
    a = LangChainAdapter()
    r = a.execute(task_id="t1", task_type="rag_query", payload={"query": "x"})
    assert r.status == "blocked"
    assert r.reason_code == "live_execution_requires_live_mode"
    # Audit log was snapshotted into the result, not leaked.
    assert a._audit.entries() == []  # noqa: SLF001


def test_lc_execute_tool_chain_requires_approval_by_default():
    """tool_chain task type asks for approval in dry-run when tools are allowed."""
    # Default-off has no allowed_tools, so any tool would be blocked.
    # Set allowed_tools to exercise the approval path.
    cfg = LangChainProviderConfig(enabled=True, mode="dry_run",
                                  allowed_tools=["summarize_doc"])
    a = LangChainAdapter(cfg)
    r = a.dry_run(task_id="t1", task_type="tool_chain",
                  payload={"tools": ["summarize_doc"]})
    assert not r.blocked
    assert r.approval_required is True
    assert any("tool_chain" in reason for reason in r.approval_reasons)


# ── LangGraph smoke ────────────────────────────────────────────────────


def test_lg_descriptor_default_off():
    desc = LangGraphAdapter().descriptor()
    assert desc.status == "disabled"
    assert desc.enabled is False


def test_lg_dry_run_agent_workflow_returns_topology():
    g = LangGraphAdapter()
    desc_payload = {
        "graph_id": "code_review_v1",
        "nodes": [
            {"id": "n1", "kind": "llm"},
            {"id": "n2", "kind": "tool", "tool_ref": "search_code"},
            {"id": "n3", "kind": "end"},
        ],
        "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
    }
    r = g.dry_run(task_id="t1", task_type="agent_workflow",
                  payload={"graph_descriptor": desc_payload})
    assert isinstance(r, DryRunResult)
    assert not r.blocked
    # The plan lists every node as an action 'node:<id>'.
    actions = [s["action"] for s in r.plan_steps]
    assert "node:n1" in actions
    assert "node:n2" in actions
    assert "node:n3" in actions
    # The descriptor's plan is at least as long as the node count.
    assert len(r.plan_steps) >= 3


def test_lg_dry_run_with_human_gate_node_requires_approval():
    g = LangGraphAdapter()
    desc_payload = {
        "graph_id": "approve_v1",
        "nodes": [
            {"id": "n1", "kind": "llm"},
            {"id": "n2", "kind": "human_gate"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    r = g.dry_run(task_id="t1", task_type="agent_workflow",
                  payload={"graph_descriptor": desc_payload})
    assert r.approval_required
    assert any("human_gate" in reason for reason in r.approval_reasons)


def test_lg_dry_run_with_high_risk_tool_node_requires_approval():
    """A tool node whose ref is in the high-risk set requires approval."""
    cfg = LangGraphProviderConfig(
        enabled=True, mode="dry_run",
        human_in_loop_required_for=["shell"],
    )
    g = LangGraphAdapter(cfg)
    desc_payload = {
        "graph_id": "shell_run",
        "nodes": [
            {"id": "n1", "kind": "llm"},
            {"id": "n2", "kind": "tool", "tool_ref": "shell"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    r = g.dry_run(task_id="t1", task_type="agent_workflow",
                  payload={"graph_descriptor": desc_payload})
    assert r.approval_required
    assert any("n2" in reason for reason in r.approval_reasons)


def test_lg_dry_run_unknown_task_type_blocked():
    g = LangGraphAdapter()
    r = g.dry_run(task_id="t1", task_type="unsupported", payload={})
    assert r.blocked


def test_lg_execute_default_off_returns_blocked():
    g = LangGraphAdapter()
    r = g.execute(task_id="t1", task_type="agent_workflow", payload={})
    assert r.status == "blocked"
    assert r.reason_code == "live_execution_requires_live_mode"
    # Audit isolation holds.
    assert g._audit.entries() == []  # noqa: SLF001


# ── Audit isolation across mixed-mode use ─────────────────────────────


def test_mixed_dry_run_and_execute_does_not_leak_audit():
    """Sequential dry_run + execute must not bleed trace events."""
    a = LangChainAdapter()
    a.dry_run(task_id="t1", task_type="rag_query", payload={"query": "x"})
    a.execute(task_id="t2", task_type="rag_query", payload={"query": "y"})
    a.dry_run(task_id="t3", task_type="rag_query", payload={"query": "z"})
    # After three calls, the internal log must still be empty —
    # each call snapshotted its own events into the result.
    assert a._audit.entries() == []  # noqa: SLF001
