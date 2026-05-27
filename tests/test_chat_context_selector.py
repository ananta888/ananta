"""Tests for ChatContextSelector — T05.03."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.chat_context_selector import ChatContextSelector
from agent.services.heuristic_runtime.chat_query_classifier import IntentKind
from agent.services.heuristic_runtime.decision_context import DecisionContext

clf = ChatContextSelector()


def _ctx(artifacts=None, goal=None, task=None, scopes=None):
    return DecisionContext(
        source_surface="chat_codecompass",
        ai_status="offline",
        selected_artifacts=artifacts or [],
        active_goal_id=goal,
        active_task_id=task,
        allowed_source_scopes=scopes or [],
    )


# ── Ranking ───────────────────────────────────────────────────────────────────

def test_selected_artifacts_tier1():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=["src/main.py", "src/auth.py"]))
    assert not result.is_no_good_match
    assert "src/main.py" in result.selected_refs


def test_active_goal_tier2():
    result = clf.select(IntentKind.GENERAL_PROJECT_QUESTION, _ctx(goal="g1"))
    assert not result.is_no_good_match
    assert "goal:g1" in result.selected_refs


def test_active_task_also_included():
    result = clf.select(IntentKind.GENERAL_PROJECT_QUESTION, _ctx(goal="g1", task="t1"))
    assert "goal:g1" in result.selected_refs
    assert "task:t1" in result.selected_refs


def test_sourcepack_for_explain_file_intent():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx())
    assert any("sourcepack" in r for r in result.selected_refs)


def test_helpcenter_for_error_intent():
    result = clf.select(IntentKind.EXPLAIN_ERROR, _ctx())
    assert any("helpcenter" in r for r in result.selected_refs)


def test_artifacts_rank_higher_than_goal():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=["ref1"], goal="g1"))
    assert result.selected_refs[0] == "ref1"


# ── Budget limiting ───────────────────────────────────────────────────────────

def test_max_5_refs():
    artifacts = [f"ref{i}" for i in range(10)]
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=artifacts))
    assert len(result.selected_refs) <= 5


def test_budget_used_is_positive():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=["src/main.py"]))
    assert result.budget_used > 0


# ── Security deny ─────────────────────────────────────────────────────────────

def test_security_deny_overrides_ranking():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(scopes=["secret"]))
    assert result.is_no_good_match
    assert result.security_denied


def test_credential_scope_denied():
    result = clf.select(IntentKind.FIND_SYMBOL, _ctx(scopes=["credential"]))
    assert result.security_denied


# ── no_good_match ─────────────────────────────────────────────────────────────

def test_unknown_intent_no_context_is_no_match():
    result = clf.select(IntentKind.UNKNOWN, _ctx())
    assert result.is_no_good_match


def test_ranking_explanation_present():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=["ref1"]))
    assert isinstance(result.ranking_explanation, str)
    assert len(result.ranking_explanation) > 0


def test_to_dict():
    result = clf.select(IntentKind.EXPLAIN_FILE, _ctx(artifacts=["ref1"]))
    d = result.to_dict()
    assert "selected_refs" in d
    assert "budget_used" in d
    assert "ranking_explanation" in d
