"""Tests for WorkflowPolicyGate default-deny + allowlist enforcement (LCG-011).

Regression guard for the 67c3cacf fix: empty allowed_tools used to mean
'everything allowed' (inverted secure default). It must now mean DENY.
"""
from __future__ import annotations

import pytest

from worker.adapters.workflow_policy_gate import WorkflowPolicyGate

_ALLOWED = frozenset({"summarize_doc", "search_code"})


@pytest.fixture
def gate_default() -> WorkflowPolicyGate:
    """Default-config gate: nothing allowed."""
    return WorkflowPolicyGate()


@pytest.fixture
def gate_with_allowlist() -> WorkflowPolicyGate:
    return WorkflowPolicyGate(allowed_tools=set(_ALLOWED))


# ── Default-deny ────────────────────────────────────────────────────────


def test_default_deny_empty_allowlist_blocks_safe_tool(gate_default):
    decision = gate_default.check_tool("summarize_doc")
    assert decision["allowed"] is False
    assert decision["reason"] == "default_deny_empty_allowlist"


def test_default_deny_blocks_every_tool(gate_default):
    decisions = [gate_default.check_tool(t) for t in ("read", "write", "exec", "search")]
    for d in decisions:
        assert d["allowed"] is False


# ── Hard-deny list wins over everything ─────────────────────────────────


@pytest.mark.parametrize("tool", [
    "exec_shell", "write_file", "delete_file",
    "read_file_arbitrary", "http_request", "network_scan", "spawn_process",
])
def test_hard_deny_always_blocks(gate_with_allowlist, tool):
    """Even when allowlisted, hard-deny tools must be blocked.

    We test with an allowlist to prove the order: hard-deny is checked
    first, so adding a hard-deny tool to the allowlist cannot bypass it.
    """
    gate_with_allowlist._allowed_tools.add(tool)  # noqa: SLF001 — explicit test setup
    decision = gate_with_allowlist.check_tool(tool)
    assert decision["allowed"] is False
    assert decision["reason"] == "always_blocked"


# ── Allowlist enforcement ──────────────────────────────────────────────


def test_allowlisted_tool_passes(gate_with_allowlist):
    assert gate_with_allowlist.check_tool("summarize_doc")["allowed"] is True
    assert gate_with_allowlist.check_tool("search_code")["allowed"] is True


def test_non_allowlisted_tool_blocked(gate_with_allowlist):
    decision = gate_with_allowlist.check_tool("not_in_allowlist")
    assert decision["allowed"] is False
    assert decision["reason"] == "not_in_allowlist"


# ── Network gate ───────────────────────────────────────────────────────


def test_network_blocked_when_external_calls_disabled(gate_default):
    decision = gate_default.check_network("https://api.example.com/x")
    assert decision["allowed"] is False
    assert decision["reason"] == "external_calls_blocked"


def test_network_allowed_when_external_calls_enabled():
    gate = WorkflowPolicyGate(external_calls_allowed=True)
    decision = gate.check_network("https://api.example.com/x")
    assert decision["allowed"] is True
    assert decision["reason"] == "external_calls_allowed"


# ── Human-required predicate ──────────────────────────────────────────


def test_requires_human_high_risk_tool():
    gate = WorkflowPolicyGate()
    assert gate.requires_human("shell") is True
    assert gate.requires_human("patch") is True
    assert gate.requires_human("read") is False


def test_requires_human_explicit_action():
    gate = WorkflowPolicyGate(human_required_actions={"deploy_prod"})
    assert gate.requires_human("deploy_prod") is True
    assert gate.requires_human("read") is False


# ── Decision log ───────────────────────────────────────────────────────


def test_decisions_log_accumulates(gate_with_allowlist):
    gate_with_allowlist.check_tool("summarize_doc")
    gate_with_allowlist.check_tool("exec_shell")
    gate_with_allowlist.check_tool("not_allowed")
    log = gate_with_allowlist.decisions_log()
    assert len(log) == 3
    assert [d["reason"] for d in log] == [
        "allowlisted", "always_blocked", "not_in_allowlist",
    ]


def test_reset_clears_decision_log(gate_with_allowlist):
    gate_with_allowlist.check_tool("summarize_doc")
    assert len(gate_with_allowlist.decisions_log()) == 1
    gate_with_allowlist.reset()
    assert gate_with_allowlist.decisions_log() == []


# ── Encapsulation: public accessor for human-required actions ────────


def test_human_required_actions_is_frozen_view():
    """The public view is a frozenset, not the internal mutable set.

    This documents the encapsulation intent: callers must use
    `requires_human()` rather than mutating the set directly. Python
    cannot enforce this without a property that returns a copy, so
    we just assert the type contract here.
    """
    gate = WorkflowPolicyGate(human_required_actions={"shell", "patch"})
    view = gate.human_required_actions
    assert isinstance(view, frozenset)
    assert view == frozenset({"shell", "patch"})
    # Mutating the view must raise AttributeError (frozenset has no add).
    with pytest.raises(AttributeError):
        view.add("network")  # type: ignore[attr-defined]
