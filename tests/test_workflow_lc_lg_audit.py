"""Tests for per-task audit isolation in LangChain/LangGraph adapters (LCG-018).

Regression guard for the 67c3cacf fix: the shared WorkflowAuditLog used
to accumulate events from every dry_run and execute call, leaking
traces across tasks. Each task must now see only its own events.
"""
from __future__ import annotations

import pytest

from agent.providers.lc_lg import (
    LangChainProviderConfig,
    LangGraphProviderConfig,
)
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.workflow_audit import WorkflowAuditLog


# ── WorkflowAuditLog primitive ────────────────────────────────────────


def test_audit_log_snapshot_returns_and_clears():
    log = WorkflowAuditLog(adapter_id="adapter.test")
    log.log("event_a", x=1)
    log.log("event_b", y=2)
    snap = log.snapshot()
    assert len(snap) == 2
    assert snap[0]["event"] == "event_a"
    assert snap[1]["event"] == "event_b"
    # Atomic reset
    assert log.entries() == []
    # Second snapshot is empty
    assert log.snapshot() == []


def test_audit_log_entries_is_copy():
    log = WorkflowAuditLog(adapter_id="adapter.test")
    log.log("a")
    e = log.entries()
    e.clear()
    assert log.entries()  # log not affected


# ── Per-task isolation: LangChainAdapter.dry_run ──────────────────────


def test_lc_dry_run_does_not_leak_into_next_task():
    a = LangChainAdapter()
    r1 = a.dry_run(task_id="t1", task_type="rag_query", payload={"query": "foo"})
    r2 = a.dry_run(task_id="t2", task_type="rag_query", payload={"query": "bar"})
    t1_trace = r1.metadata["dry_run_audit_trace"]
    t2_trace = r2.metadata["dry_run_audit_trace"]
    assert all(e.get("task_id") == "t1" for e in t1_trace)
    assert all(e.get("task_id") == "t2" for e in t2_trace)
    # Each task has at least the start + complete pair.
    assert {e["event"] for e in t1_trace} >= {"dry_run_start", "dry_run_complete"}
    assert {e["event"] for e in t2_trace} >= {"dry_run_start", "dry_run_complete"}


def test_lc_dry_run_trace_attached_to_result_only():
    a = LangChainAdapter()
    r = a.dry_run(task_id="t1", task_type="rag_query", payload={"query": "x"})
    assert "dry_run_audit_trace" in r.metadata
    # Internal audit log is now empty — next call starts clean.
    assert a._audit.entries() == []  # noqa: SLF001 — regression guard


# ── Per-task isolation: LangGraphAdapter.dry_run ──────────────────────


def test_lg_dry_run_does_not_leak_into_next_task():
    g = LangGraphAdapter()
    gr1 = g.dry_run(task_id="t1", task_type="agent_workflow", payload={})
    gr2 = g.dry_run(task_id="t2", task_type="agent_workflow", payload={})
    t1_trace = gr1.metadata["dry_run_audit_trace"]
    t2_trace = gr2.metadata["dry_run_audit_trace"]
    assert all(e.get("task_id") == "t1" for e in t1_trace)
    assert all(e.get("task_id") == "t2" for e in t2_trace)


def test_lg_dry_run_internal_log_empty_after_return():
    g = LangGraphAdapter()
    g.dry_run(task_id="t1", task_type="agent_workflow", payload={})
    assert g._audit.entries() == []  # noqa: SLF001


# ── execute() also resets the audit log ───────────────────────────────


def test_lc_execute_starts_with_clean_audit_log():
    a = LangChainAdapter()
    # Call dry_run first to populate the (would-be) shared log.
    a.dry_run(task_id="t1", task_type="rag_query", payload={"query": "x"})
    # execute with default-off provider must block; even the blocked
    # path must have a clean internal log afterwards.
    blocked = a.execute(task_id="t2", task_type="rag_query",
                        payload={"query": "y"})
    assert blocked.status == "blocked"
    # The execute-path's events are snapshot'd into the result; the
    # internal log is empty for the next call.
    assert a._audit.entries() == []  # noqa: SLF001


# ── Documented behaviour: snapshot() is the only public way to consume


def test_audit_log_snapshot_is_documented_contract():
    """snapshot() returns the events for THIS task and clears the log.

    Entries() does NOT clear. If a test starts using entries() in
    production code instead of snapshot(), cross-task leakage comes
    back. This test documents the intent and the danger.
    """
    log = WorkflowAuditLog(adapter_id="x")
    log.log("e1")
    assert log.entries() == [log.entries()[0]]  # entries() does not clear
    log.clear()
    assert log.snapshot() == []
