"""SEH-018: promotion, canary and rollback controller."""

from __future__ import annotations

import pytest

from agent.services.harness_gates import GateEngine, threshold_gate
from agent.services.harness_pareto import Candidate, ParetoSelector
from agent.services.harness_promotion import PromotionController

SELECTOR = ParetoSelector(maximize=["task_correctness"], minimize=["total_token_cost"])


def candidate(cid, *, correctness, cost):
    return Candidate(candidate_id=cid, objectives={"task_correctness": correctness, "total_token_cost": cost})


def gate_report(*, disqualified=False, reason=None):
    from agent.services.harness_gates import GateOutcome, GateReport

    outcome = GateOutcome("correctness", not disqualified, reason or "ok", True)
    return GateReport((outcome,), disqualified, reason)


def controller():
    return PromotionController(SELECTOR, canary_fraction=0.2)


def test_promotion_requires_a_gate_report():
    decision = controller().decide(candidate=candidate("c", correctness=0.9, cost=50),
                                   baseline=candidate("b", correctness=0.8, cost=100), gate_report=None)
    assert decision.action == "reject" and decision.reason == "promotion_requires_gate_report"


def test_gate_failure_blocks_promotion_regardless_of_fitness():
    decision = controller().decide(candidate=candidate("c", correctness=0.99, cost=1),
                                   baseline=candidate("b", correctness=0.5, cost=999),
                                   gate_report=gate_report(disqualified=True, reason="gate_regression"))
    assert decision.action == "reject" and decision.reason == "gate_disqualified:gate_regression"


def test_dominating_candidate_is_promoted():
    decision = controller().decide(candidate=candidate("c", correctness=0.9, cost=50),
                                   baseline=candidate("b", correctness=0.8, cost=100), gate_report=gate_report())
    assert decision.action == "promote"


def test_dominated_candidate_is_rejected_and_tradeoff_goes_to_canary():
    rejected = controller().decide(candidate=candidate("c", correctness=0.7, cost=200),
                                   baseline=candidate("b", correctness=0.8, cost=100), gate_report=gate_report())
    assert rejected.action == "reject" and rejected.reason == "dominated_by_baseline"
    canary = controller().decide(candidate=candidate("c", correctness=0.95, cost=200),
                                 baseline=candidate("b", correctness=0.8, cost=100), gate_report=gate_report())
    assert canary.action == "canary" and canary.canary_fraction == 0.2


def test_rollback_requires_a_reason():
    decision = controller().rollback(promoted_id="c", baseline_id="b", reason="latency regression")
    assert decision.action == "rollback" and decision.reason == "latency regression"
    with pytest.raises(ValueError, match="promotion_rollback_reason_required"):
        controller().rollback(promoted_id="c", baseline_id="b", reason="")


def test_invalid_canary_fraction_is_rejected():
    with pytest.raises(ValueError, match="promotion_canary_fraction_invalid"):
        PromotionController(SELECTOR, canary_fraction=0.0)


def test_gate_engine_integration_uses_real_hard_gates():
    report = GateEngine([threshold_gate("correctness", "task_correctness", 0.8)]).evaluate({"task_correctness": 0.5})
    decision = controller().decide(candidate=candidate("c", correctness=0.5, cost=10),
                                   baseline=candidate("b", correctness=0.8, cost=100), gate_report=report)
    assert decision.action == "reject"
