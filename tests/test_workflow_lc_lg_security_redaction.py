"""Security tests for LangChain/LangGraph adapter redaction (LCG-054, LCG-055).

LCG-025 notes referenced tests/security/test_langchain_langgraph_secret_redaction.py
which does NOT exist. This file is the canonical implementation.

Covers:
- WorkflowAuditLog.log() redacts string kwargs before storing (LCG-054)
- LangChainAdapter._build_prompt() redacts query before prompt-build (LCG-055)
- LangGraphAdapter._build_node_prompt() redacts prior llm_responses (LCG-055)
- execution_trace in WorkflowArtifactResult contains no sk-xxx patterns
- to_safe_dict() strips secret_refs (regression guard from LCG-003/004)
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from agent.providers.lc_lg import LangChainProviderConfig, LangGraphProviderConfig
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.workflow_audit import WorkflowAuditLog


_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{8,}")


# ── WorkflowAuditLog redaction (LCG-054) ──────────────────────────────────────


def test_audit_log_redacts_secret_in_string_kwarg():
    """Secrets in log kwargs must not appear in stored entries.

    Uses 'token' as the kwarg key (known sensitive key in redaction map)
    and a 20+ char sk- value so both key-based and pattern-based redaction can fire.
    """
    audit = WorkflowAuditLog("adapter.test")
    # 'token' is in the default sensitive key map → value gets replaced with ***REDACTED_TOKEN***
    audit.log("test_event", token="sk-abc123xyzABCDEFGHIJ")
    entries = audit.entries()
    assert len(entries) == 1
    stored = entries[0].get("token", "")
    assert "sk-abc123xyzABCDEFGHIJ" not in str(stored), (
        f"Secret was not redacted from audit log: {stored!r}"
    )


def test_audit_log_does_not_redact_event_and_ts():
    """'event' and 'ts' fields are never passed through redact()."""
    audit = WorkflowAuditLog("adapter.test")
    audit.log("node_enter", node="my_node", kind="llm")
    entries = audit.entries()
    assert entries[0]["event"] == "node_enter"
    assert "ts" in entries[0]
    assert entries[0]["adapter_id"] == "adapter.test"


def test_audit_log_non_string_values_pass_through():
    """Non-string values (int, bool, list) are stored as-is."""
    audit = WorkflowAuditLog("adapter.test")
    audit.log("test_event", step_count=42, blocked=False, nodes=["a", "b"])
    entries = audit.entries()
    assert entries[0]["step_count"] == 42
    assert entries[0]["blocked"] is False
    assert entries[0]["nodes"] == ["a", "b"]


def test_audit_snapshot_clears_after_redaction():
    """snapshot() works correctly even with redaction."""
    audit = WorkflowAuditLog("adapter.test")
    audit.log("ev1", token="sk-secret999")
    audit.log("ev2", task_id="t1")
    snap = audit.snapshot()
    assert len(snap) == 2
    assert "sk-secret999" not in str(snap[0].get("token", ""))
    assert len(audit.entries()) == 0


# ── LangChainAdapter._build_prompt() secret guard (LCG-055) ──────────────────


def test_lc_build_prompt_redacts_secret_in_query():
    """_build_prompt() must not embed sk-xxx secrets in the prompt string.

    The redact() function redacts sk- tokens with 20+ chars (TOKEN regex).
    """
    cfg = LangChainProviderConfig(enabled=True, mode="local_live")
    adapter = LangChainAdapter(cfg)
    # Use a 20+ char sk- secret so the TOKEN regex fires
    secret = "sk-ABCD1234EFGH5678IJKL"  # 20 chars after sk-
    payload = {"query": f"please use key {secret} to authenticate"}
    prompt = adapter._build_prompt("rag_query", payload, [])  # noqa: SLF001
    assert secret not in prompt, (
        f"Secret was not redacted from prompt: {prompt!r}"
    )


def test_lc_dry_run_plan_steps_no_secret():
    """dry_run plan_steps must not embed secrets from payload query."""
    adapter = LangChainAdapter()
    # dry_run plan_steps don't include the query text directly, so any sk- is safe
    result = adapter.dry_run(
        task_id="t1",
        task_type="rag_query",
        payload={"query": "find sk-ABCD1234EFGH5678IJKL in the codebase"},
    )
    plan_text = str(result.plan_steps)
    assert "sk-ABCD1234EFGH5678IJKL" not in plan_text


# ── LangGraphAdapter._build_node_prompt() redaction (LCG-055) ────────────────


def test_lg_build_node_prompt_redacts_prior_responses():
    """_build_node_prompt() must not embed sk-xxx from state.llm_responses.

    The redact() function redacts sk- tokens with 20+ chars (TOKEN regex).
    """
    from worker.adapters.langgraph_adapter import LangGraphAdapter, _GraphState

    cfg = LangGraphProviderConfig(enabled=True, mode="local_live")
    adapter = LangGraphAdapter(cfg)
    state = _GraphState(graph_id="g1", task_id="t1")
    # Use a 20+ char sk- secret so the TOKEN regex fires
    secret = "sk-SUPER1234SECRET5678XY"  # 20 chars after sk-
    state.llm_responses.append({
        "node_id": "prev",
        "runner": "simplex",
        "response": f"here is the api key {secret} please use it",
    })
    node = {"id": "curr", "kind": "llm"}
    prompt = adapter._build_node_prompt(node, state)  # noqa: SLF001
    assert secret not in prompt, (
        f"Secret was not redacted from node prompt: {prompt!r}"
    )


# ── execution_trace contains no secrets (LCG-054) ─────────────────────────────


def test_lc_execution_trace_no_secrets(monkeypatch):
    """The execution_trace in WorkflowArtifactResult must not contain 20+-char sk-xxx tokens."""
    cfg = LangChainProviderConfig(enabled=True, mode="local_live",
                                  model_provider_ref="local.default")
    adapter = LangChainAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langchain_adapter.LangChainAdapter._langchain_available",
        staticmethod(lambda: False),
    )

    # Use 20+ char sk- so TOKEN regex fires when audit log stores the response
    secret = "sk-TOPSECRET1234567890"  # 20 chars after sk-
    secret_response = f"The answer involves {secret} token usage."
    with patch("agent.llm_integration.generate_text", return_value=secret_response):
        result = adapter.execute(
            task_id="t1",
            task_type="rag_query",
            payload={"query": "what token do we use?"},
        )

    trace_str = str(result.execution_trace)
    assert secret not in trace_str, (
        f"Secret leaked into execution_trace: found in {trace_str!r}"
    )


# ── to_safe_dict regression guard (LCG-003, LCG-004) ─────────────────────────


def test_lc_to_safe_dict_omits_secret_refs_regression():
    """to_safe_dict() must never include secret_refs — regression guard."""
    cfg = LangChainProviderConfig(
        enabled=True, mode="cloud_gated", external_calls_allowed=True,
        secret_refs=["vault:openai/sk-ref"],
    )
    safe = cfg.to_safe_dict()
    assert "secret_refs" not in safe


def test_lg_to_safe_dict_omits_secret_refs_regression():
    """to_safe_dict() must never include secret_refs — regression guard."""
    cfg = LangGraphProviderConfig(
        enabled=True, mode="cloud_gated", external_calls_allowed=True,
        secret_refs=["vault:anthropic/key"],
    )
    safe = cfg.to_safe_dict()
    assert "secret_refs" not in safe
