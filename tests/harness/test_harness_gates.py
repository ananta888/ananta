"""SEH-014: hard gates disqualify regardless of fitness."""

from __future__ import annotations

import pytest

from agent.services.harness_gates import GateEngine, regression_gate, security_gate, threshold_gate


def engine(**overrides):
    gates = [
        security_gate("security:prompt_injection", "prompt_injection_clean"),
        threshold_gate("correctness", "task_correctness", 0.8),
        threshold_gate("schema", "schema_valid", 1, hard=False),
        regression_gate("regression", "task_correctness", baseline=0.9, tolerance=0.05),
    ]
    return GateEngine(gates)


def test_hard_gate_failure_disqualifies_regardless_of_fitness():
    # A high fitness metric cannot rescue a failed correctness hard gate.
    report = engine().evaluate({"prompt_injection_clean": True, "task_correctness": 0.7, "schema_valid": 1,
                                "fitness_score": 100.0})
    assert report.disqualified is True and report.reason == "gate_below_threshold:task_correctness"


def test_soft_gate_failure_does_not_disqualify():
    report = engine().evaluate({"prompt_injection_clean": True, "task_correctness": 0.9, "schema_valid": 0})
    assert report.disqualified is False
    schema = next(outcome for outcome in report.outcomes if outcome.gate_id == "schema")
    assert schema.passed is False and schema.hard is False


def test_security_gate_failure_is_always_hard():
    report = engine().evaluate({"prompt_injection_clean": False, "task_correctness": 0.9, "schema_valid": 1})
    assert report.disqualified is True and report.reason == "gate_security_failed"
    assert "security:prompt_injection" in engine().security_gate_ids()


def test_missing_metric_fails_closed():
    report = engine().evaluate({})
    assert report.disqualified is True and report.reason == "gate_security_failed"


def test_gate_error_fails_closed():
    def boom(_candidate):
        raise RuntimeError("checker bug")

    from agent.services.harness_gates import HarnessGate

    report = GateEngine([HarnessGate("flaky", True, boom)]).evaluate({})
    assert report.disqualified is True and report.reason == "gate_error"


def test_duplicate_gate_ids_are_rejected():
    with pytest.raises(ValueError, match="gate_duplicate"):
        GateEngine([threshold_gate("x", "m", 0), threshold_gate("x", "m", 1)])


def test_report_is_structured_and_reproducible():
    first = engine().evaluate({"prompt_injection_clean": True, "task_correctness": 0.9, "schema_valid": 1})
    second = engine().evaluate({"prompt_injection_clean": True, "task_correctness": 0.9, "schema_valid": 1})
    assert first.to_mapping() == second.to_mapping()
    assert {outcome["gate_id"] for outcome in first.to_mapping()["outcomes"]} == {
        "security:prompt_injection", "correctness", "schema", "regression"}
