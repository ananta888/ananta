"""Tests for ChatQueryClassifier — T05.02."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.chat_query_classifier import (
    ChatQueryClassifier,
    ClassificationResult,
    IntentKind,
)
from agent.services.heuristic_runtime.decision_context import DecisionContext


def _ctx(artifacts=None):
    return DecisionContext(
        source_surface="chat_codecompass",
        ai_status="offline",
        selected_artifacts=artifacts or [],
    )


clf = ChatQueryClassifier()


# ── explain_file ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "Erkläre was diese Datei macht",
    "explain this module",
    "what does this file do",
    "was ist in diesem Modul",
    "zeig mir eine Übersicht",
    "show me an overview of this",
])
def test_explain_file_intent(query):
    result = clf.classify(query, _ctx())
    assert result.intent_kind == IntentKind.EXPLAIN_FILE, f"failed for: {query}"
    assert result.confidence >= 0.7


# ── find_symbol ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "wo ist MyClass definiert",
    "where is the function parseInput",
    "find the method getUserById",
    "suche nach Symbol Authenticator",
    "where is class DataManager declared",
    "definition of processPayment",
])
def test_find_symbol_intent(query):
    result = clf.classify(query, _ctx())
    assert result.intent_kind == IntentKind.FIND_SYMBOL, f"failed for: {query}"
    assert result.confidence >= 0.7


# ── explain_error ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "Warum gibt es diesen Fehler",
    "error in my code",
    "exception thrown at line 42",
    "traceback shows NullPointerException",
    "why is this failing",
    "crash beim Start",
    "bug in der Authentifizierung",
])
def test_explain_error_intent(query):
    result = clf.classify(query, _ctx())
    assert result.intent_kind == IntentKind.EXPLAIN_ERROR, f"failed for: {query}"
    assert result.confidence >= 0.7


# ── todo_status ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "was sind die offenen Aufgaben",
    "todo status",
    "open tasks in this sprint",
    "was steht noch aus",
    "what's left to do",
    "zeig Fortschritt",
])
def test_todo_status_intent(query):
    result = clf.classify(query, _ctx())
    assert result.intent_kind == IntentKind.TODO_STATUS, f"failed for: {query}"


# ── helpcenter_lookup ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "how to configure this",
    "wie geht das mit der Authentifizierung",
    "guide for deployment",
    "FAQ zu Berechtigungen",
    "wie kann ich einen Benutzer anlegen",
])
def test_helpcenter_intent(query):
    result = clf.classify(query, _ctx())
    assert result.intent_kind == IntentKind.HELPCENTER_LOOKUP, f"failed for: {query}"


# ── artifact_lookup with selection ────────────────────────────────────────────

def test_selected_artifact_boosts_confidence():
    # plain phrase with no intent keyword → falls back to ARTIFACT_LOOKUP
    result = clf.classify("kannst du das bitte anschauen", _ctx(artifacts=["src/main.py"]))
    assert result.intent_kind == IntentKind.ARTIFACT_LOOKUP
    assert result.confidence == 0.9


def test_selected_artifact_plus_keyword_gives_full_confidence():
    result = clf.classify("erkläre src/main.py", _ctx(artifacts=["src/main.py"]))
    assert result.confidence == 1.0
    assert result.intent_kind == IntentKind.EXPLAIN_FILE


# ── general / unknown ─────────────────────────────────────────────────────────

def test_question_without_keywords_is_general():
    result = clf.classify("Was denkst du über dieses Projekt?", _ctx())
    assert result.intent_kind == IntentKind.GENERAL_PROJECT_QUESTION
    assert result.confidence == 0.3


def test_empty_query_is_unknown():
    result = clf.classify("", _ctx())
    assert result.intent_kind == IntentKind.UNKNOWN
    assert result.confidence == 0.0


def test_gibberish_is_unknown():
    result = clf.classify("xyzqqqq abcdef", _ctx())
    assert result.intent_kind == IntentKind.UNKNOWN


# ── confidence levels ─────────────────────────────────────────────────────────

def test_multiple_keywords_boost_confidence():
    result = clf.classify("error exception traceback", _ctx())
    assert result.intent_kind == IntentKind.EXPLAIN_ERROR
    assert result.confidence == 0.8  # 2+ matches


def test_single_keyword_confidence():
    result = clf.classify("error in code", _ctx())
    assert result.intent_kind == IntentKind.EXPLAIN_ERROR
    assert result.confidence == 0.7


# ── reason codes ─────────────────────────────────────────────────────────────

def test_reason_codes_present():
    result = clf.classify("where is MyClass", _ctx())
    assert any("find_symbol" in rc for rc in result.reason_codes)


def test_to_dict():
    result = clf.classify("error", _ctx())
    d = result.to_dict()
    assert "intent_kind" in d
    assert "confidence" in d
    assert "reason_codes" in d
