"""Tests for the notation-pattern allow-list and helpers (NOT-003)."""

from __future__ import annotations

import pytest

from agent.services.pattern_selection_policy import (
    DEFAULT_ALLOWLIST,
    NOTATION_PATTERN_IDS,
    PatternSelectionPolicy,
    is_notation_pattern,
)


# ---------------------------------------------------------------------------
# Allow-list defaults
# ---------------------------------------------------------------------------


def test_default_allowlist_includes_diagram_mermaid():
    assert "diagram_mermaid" in DEFAULT_ALLOWLIST
    assert DEFAULT_ALLOWLIST["diagram_mermaid"] >= {
        "mermaid.class",
        "mermaid.sequence",
        "mermaid.state",
        "mermaid.usecase",
        "mermaid.activity",
    }


def test_default_allowlist_includes_diagram_bpmn():
    assert "diagram_bpmn" in DEFAULT_ALLOWLIST
    assert DEFAULT_ALLOWLIST["diagram_bpmn"] >= {
        "bpmn.process",
        "bpmn.pool_lane",
        "bpmn.collaboration",
    }


# ---------------------------------------------------------------------------
# Notation-pattern helper
# ---------------------------------------------------------------------------


def test_is_notation_pattern_true_for_diagram_ids():
    for pid in [
        "mermaid.class", "mermaid.sequence", "mermaid.state",
        "mermaid.usecase", "mermaid.activity",
        "bpmn.process", "bpmn.pool_lane", "bpmn.collaboration",
    ]:
        assert is_notation_pattern(pid), f"{pid} should be a notation pattern"


def test_is_notation_pattern_false_for_code_patterns():
    for pid in ["python.strategy", "java.strategy", "ts.strategy",
                "python.function_stub", "cli.retry_wrap",
                "workflow.sequential_emit"]:
        assert not is_notation_pattern(pid), f"{pid} must not be a notation pattern"


def test_is_notation_pattern_false_for_none_and_empty():
    assert not is_notation_pattern(None)
    assert not is_notation_pattern("")


# ---------------------------------------------------------------------------
# Policy decisions for notation patterns
# ---------------------------------------------------------------------------


def test_policy_accepts_mermaid_class_with_diagram_mermaid_task_kind():
    p = PatternSelectionPolicy()
    decision = p.decide(
        pattern_id="mermaid.class",
        task_kind="diagram_mermaid",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert decision.allowed
    assert decision.risk_level == "low"


def test_policy_accepts_bpmn_collaboration_with_diagram_bpmn_task_kind():
    p = PatternSelectionPolicy()
    decision = p.decide(
        pattern_id="bpmn.collaboration",
        task_kind="diagram_bpmn",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert decision.allowed


def test_policy_rejects_mermaid_pattern_under_coding_task_kind():
    p = PatternSelectionPolicy()
    decision = p.decide(
        pattern_id="mermaid.class",
        task_kind="coding",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert not decision.allowed
    assert decision.blocked_reason is not None
    assert "not in the default allow-list" in decision.blocked_reason


def test_policy_rejects_unknown_notation_id_even_with_correct_task_kind():
    p = PatternSelectionPolicy()
    decision = p.decide(
        pattern_id="mermaid.not_a_thing",
        task_kind="diagram_mermaid",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert not decision.allowed
    assert decision.blocked_reason is not None
    assert "not in the catalogue" in decision.blocked_reason


def test_notation_patterns_are_not_marked_risky():
    """Notation patterns must never trigger the risky-pattern opt-in gate.
    They are pure generators and cannot grant privileges or mutate state."""
    p = PatternSelectionPolicy()
    risky = p.risky_pattern_ids()
    for pid in NOTATION_PATTERN_IDS:
        assert pid not in risky, f"{pid} must not be marked risky"
        decision = p.decide(
            pattern_id=pid,
            task_kind=("diagram_mermaid" if pid.startswith("mermaid.")
                       else "diagram_bpmn"),
            allow_risky_patterns=False,
            catalogue_ids=set(NOTATION_PATTERN_IDS),
        )
        assert decision.allowed
        assert decision.risk_level == "low"


# ---------------------------------------------------------------------------
# Custom policy overrides
# ---------------------------------------------------------------------------


def test_custom_policy_can_restrict_notation_patterns():
    custom = PatternSelectionPolicy(
        allowlist={
            "diagram_mermaid": {"mermaid.class"},  # only class
            "diagram_bpmn": set(),
            "other": set(),
        },
    )
    decision_class = custom.decide(
        pattern_id="mermaid.class",
        task_kind="diagram_mermaid",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert decision_class.allowed
    decision_seq = custom.decide(
        pattern_id="mermaid.sequence",
        task_kind="diagram_mermaid",
        catalogue_ids=set(NOTATION_PATTERN_IDS),
    )
    assert not decision_seq.allowed