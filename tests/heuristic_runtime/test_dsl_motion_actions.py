"""Tests für DSL v2 Action-Modell in DecisionResult.from_dsl_action()."""
import pytest

from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion


def test_from_dsl_action_suggest_target():
    """suggest_target erzeugt follow-Aktion mit target_cell als Motion."""
    action = {
        "kind": "suggest_target",
        "confidence": 0.9,
        "target_cell": {"x": 3, "y": -1},
        "reason_codes": ["mouse_near"],
    }
    result = DecisionResult.from_dsl_action(action, strategy_id="test_h")
    assert result.action_kind == "follow"
    assert result.confidence == 0.9
    assert result.source == "heuristic"
    assert result.suggested_motion is not None
    assert result.suggested_motion.dx == 3
    assert result.suggested_motion.dy == -1
    assert "mouse_near" in result.reason_codes
    assert result.strategy_id == "test_h"


def test_from_dsl_action_fast_target():
    """fast_target erzeugt follow-Aktion."""
    action = {"kind": "fast_target", "confidence": 0.8}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "follow"


def test_from_dsl_action_smooth_follow():
    """smooth_follow erzeugt follow-Aktion."""
    action = {"kind": "smooth_follow", "confidence": 0.7}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "follow"


def test_from_dsl_action_follow_artifact():
    """follow_artifact erzeugt follow-Aktion."""
    action = {"kind": "follow_artifact", "confidence": 0.75}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "follow"


def test_from_dsl_action_without_target_cell():
    """Keine target_cell → SuggestedMotion ist None."""
    action = {"kind": "suggest_target", "confidence": 0.5}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "follow"
    assert result.suggested_motion is None


def test_from_dsl_action_lurk_near():
    """lurk_near erzeugt lurk-Aktion."""
    action = {"kind": "lurk_near", "confidence": 0.6}
    result = DecisionResult.from_dsl_action(action, strategy_id="lurk_strategy")
    assert result.action_kind == "lurk"
    assert result.source == "heuristic"
    assert result.strategy_id == "lurk_strategy"


def test_from_dsl_action_explain_target():
    """explain_target erzeugt follow-Aktion mit reason_code."""
    action = {"kind": "explain_target", "confidence": 0.65, "reason_codes": ["artifact_selected"]}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "follow"
    assert "explain_target" in result.reason_codes
    assert "artifact_selected" in result.reason_codes


def test_from_dsl_action_no_action():
    """no_action erzeugt no_good_match."""
    action = {"kind": "no_action"}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "no_action"
    assert result.answer_kind == "no_good_match"
    assert result.confidence == 0.0


def test_from_dsl_action_unknown_kind_fallback():
    """Unbekannte kind → no_good_match."""
    action = {"kind": "unknown_kind_xyz"}
    result = DecisionResult.from_dsl_action(action)
    assert result.action_kind == "no_action"
    assert result.answer_kind == "no_good_match"


def test_from_dsl_action_default_confidence():
    """Fehlende confidence → Default 0.8."""
    action = {"kind": "follow_artifact"}
    result = DecisionResult.from_dsl_action(action)
    assert result.confidence == 0.8


def test_from_dsl_action_empty_reason_codes():
    """Fehlende reason_codes → leere Liste."""
    action = {"kind": "follow_artifact", "confidence": 0.7}
    result = DecisionResult.from_dsl_action(action)
    assert result.reason_codes == []


def test_from_dsl_action_strategy_id_propagated():
    """strategy_id wird korrekt propagiert."""
    action = {"kind": "lurk_near"}
    result = DecisionResult.from_dsl_action(action, strategy_id="my_heuristic_v2")
    assert result.strategy_id == "my_heuristic_v2"


def test_from_dsl_action_no_strategy_id():
    """strategy_id ist None wenn nicht angegeben."""
    action = {"kind": "follow_artifact"}
    result = DecisionResult.from_dsl_action(action)
    assert result.strategy_id is None


def test_from_dsl_action_is_heuristic():
    """Alle DSL-Actions haben source='heuristic'."""
    for kind in ["suggest_target", "follow_artifact", "lurk_near", "smooth_follow", "fast_target", "explain_target", "no_action"]:
        result = DecisionResult.from_dsl_action({"kind": kind})
        assert result.source == "heuristic", f"Expected heuristic source for kind={kind}"
