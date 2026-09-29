"""Tests for ContextBudgetPolicyService — T03."""
from __future__ import annotations

import pytest
from agent.services.context_budget_policy_service import (
    ContextBudgetDecision,
    MODES,
    decide_context_budget,
)


def _decide(**kwargs) -> ContextBudgetDecision:
    return decide_context_budget(**kwargs)


# ── Mode selection ────────────────────────────────────────────────────────────

def test_smalltalk_intent_gives_safe_minimal_chat():
    decision = _decide(intent="smalltalk")
    assert decision.mode == "safe_minimal_chat"


def test_code_question_gives_project_chat():
    decision = _decide(intent="code_question")
    assert decision.mode == "project_chat"


def test_tool_request_gives_tool_enabled_chat():
    decision = _decide(intent="tool_request")
    assert decision.mode == "tool_enabled_chat"


def test_analysis_gives_deep_analysis():
    decision = _decide(intent="analysis")
    assert decision.mode == "deep_analysis"


def test_unknown_intent_fail_closed_gives_safe_minimal():
    decision = _decide(intent="unknown", fail_closed=True)
    assert decision.mode == "safe_minimal_chat"


def test_unknown_intent_not_fail_closed_gives_project_chat():
    decision = _decide(intent="unknown", fail_closed=False)
    assert decision.mode == "project_chat"


def test_empty_intent_fail_closed_gives_safe_minimal():
    decision = _decide(intent="", fail_closed=True)
    assert decision.mode == "safe_minimal_chat"


def test_no_model_profile_fail_closed_gives_safe_minimal():
    decision = _decide(intent="", model_profile=None, fail_closed=True)
    assert decision.mode == "safe_minimal_chat"
    assert "no_model_profile_fail_closed" in decision.reason_codes


# ── Blocked / allowed sources ─────────────────────────────────────────────────

def test_safe_minimal_blocks_rag():
    decision = _decide(intent="smalltalk")
    assert "rag" in decision.blocked_context_sources
    assert "tool_schemas" in decision.blocked_context_sources
    assert "full_history" in decision.blocked_context_sources
    assert "compaction" in decision.blocked_context_sources


def test_safe_minimal_allows_user_message_and_short_history():
    decision = _decide(intent="smalltalk")
    assert "user_message" in decision.allowed_context_sources
    assert "short_history" in decision.allowed_context_sources


def test_tool_enabled_allows_tool_schemas():
    decision = _decide(intent="tool_request")
    assert "tool_schemas" in decision.allowed_context_sources


def test_deep_analysis_allows_all_sources():
    decision = _decide(intent="analysis")
    assert "rag" in decision.allowed_context_sources
    assert "tool_schemas" in decision.allowed_context_sources
    assert "full_history" in decision.allowed_context_sources
    assert decision.blocked_context_sources == []


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_decision_ref_is_set():
    decision = _decide(intent="smalltalk")
    assert decision.decision_ref  # non-empty UUID


def test_fail_closed_flag():
    decision = _decide(intent="smalltalk", fail_closed=True)
    assert decision.fail_closed is True


def test_max_tokens_passed_through():
    decision = _decide(intent="code_question", max_input_tokens=16384, max_output_tokens=4096)
    assert decision.max_input_tokens == 16384
    assert decision.max_output_tokens == 4096


def test_as_dict_roundtrip():
    decision = _decide(intent="tool_request")
    d = decision.as_dict()
    assert d["mode"] == "tool_enabled_chat"
    assert isinstance(d["allowed_context_sources"], list)
    assert isinstance(d["reason_codes"], list)
    assert "decision_ref" in d


def test_all_modes_are_valid():
    for mode in MODES:
        assert isinstance(mode, str)
