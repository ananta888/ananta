"""Live integration tests for LangChain/LangGraph adapters (LCG-058).

These tests require a running Ollama instance. They are marked
@pytest.mark.integration and skipped unless ANANTA_OLLAMA_URL is set.

Run with:
    ANANTA_OLLAMA_URL=http://localhost:11434 pytest tests/test_workflow_lc_lg_live_integration.py -m integration
"""
from __future__ import annotations

import os

import pytest

from agent.providers.lc_lg import LangChainProviderConfig, LangGraphProviderConfig
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.langgraph_adapter import LangGraphAdapter


# ── Skip fixture ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def require_ollama_url():
    if not os.environ.get("ANANTA_OLLAMA_URL"):
        pytest.skip("ANANTA_OLLAMA_URL not set — skipping live integration tests")


# ── LangChain live tests ───────────────────────────────────────────────────────


@pytest.mark.integration
def test_lc_live_rag_query_returns_success():
    """LangChainAdapter in local_live mode returns a successful artifact."""
    cfg = LangChainProviderConfig(
        enabled=True,
        mode="local_live",
        model_provider_ref="local.default",
        timeout_seconds=60,
        max_steps=5,
    )
    adapter = LangChainAdapter(cfg)
    result = adapter.execute(
        task_id="live-lc-t1",
        task_type="rag_query",
        payload={"query": "what is Python?"},
    )
    assert result.status in ("success", "partial"), (
        f"Expected success or partial, got: {result.status!r} — {result.error!r}"
    )


@pytest.mark.integration
def test_lc_live_artifact_content_is_nonempty_string():
    """The first artifact's content must be a non-empty string."""
    cfg = LangChainProviderConfig(
        enabled=True,
        mode="local_live",
        model_provider_ref="local.default",
        timeout_seconds=60,
        max_steps=5,
    )
    adapter = LangChainAdapter(cfg)
    result = adapter.execute(
        task_id="live-lc-t2",
        task_type="summarize",
        payload={"query": "Summarize: Python is a high-level programming language."},
    )
    if result.status in ("success", "partial"):
        assert result.artifacts, "Expected at least one artifact"
        content = result.artifacts[0].get("content", "")
        assert content, f"Artifact content is empty: {result.artifacts[0]!r}"


@pytest.mark.integration
def test_lc_live_execution_trace_contains_execute_complete():
    """execution_trace must have an execute_complete event on success."""
    cfg = LangChainProviderConfig(
        enabled=True,
        mode="local_live",
        model_provider_ref="local.default",
        timeout_seconds=60,
        max_steps=5,
    )
    adapter = LangChainAdapter(cfg)
    result = adapter.execute(
        task_id="live-lc-t3",
        task_type="rag_query",
        payload={"query": "hello world"},
    )
    event_names = {e.get("event") for e in result.execution_trace}
    assert "execute_complete" in event_names or result.status == "failed", (
        f"execute_complete not in trace: {event_names}"
    )


@pytest.mark.integration
def test_lc_live_timeout_budget_causes_failure():
    """A very short timeout must surface as budget_timeout failure."""
    cfg = LangChainProviderConfig(
        enabled=True,
        mode="local_live",
        model_provider_ref="local.default",
        timeout_seconds=1,
        max_steps=100,
    )
    adapter = LangChainAdapter(cfg)
    result = adapter.execute(
        task_id="live-lc-timeout",
        task_type="rag_query",
        payload={"query": "compute the meaning of life in full detail"},
    )
    assert result.status == "failed"
    assert result.reason_code in ("budget_timeout", "llm_call_failed"), (
        f"Expected timeout failure, got: {result.reason_code!r}"
    )


# ── LangGraph live tests ───────────────────────────────────────────────────────


@pytest.mark.integration
def test_lg_live_agent_workflow_success():
    """LangGraphAdapter in local_live mode returns a successful result."""
    cfg = LangGraphProviderConfig(
        enabled=True,
        mode="local_live",
        model_provider_ref="local.default",
        timeout_seconds=60,
        max_iterations=5,
    )
    adapter = LangGraphAdapter(cfg)
    payload = {
        "graph_descriptor": {
            "nodes": [
                {"id": "draft", "kind": "llm"},
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "draft", "to": "end"}],
        },
        "query": "what is a Python list?",
    }
    result = adapter.execute(
        task_id="live-lg-t1",
        task_type="agent_workflow",
        payload=payload,
    )
    assert result.status in ("success", "partial"), (
        f"Expected success or partial, got: {result.status!r} — {result.error!r}"
    )
