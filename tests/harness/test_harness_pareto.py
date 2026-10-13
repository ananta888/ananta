"""SEH-015: Pareto dominance, front retention, profiles and confidence."""

from __future__ import annotations

import pytest

from agent.services.harness_pareto import Candidate, ParetoSelector

SELECTOR = ParetoSelector(maximize=["task_correctness", "holdout_generalization_score"],
                          minimize=["total_token_cost", "latency_p95"])


def candidate(cid, *, correctness, cost, latency=100.0, confidence=1.0, variance=0.0):
    return Candidate(candidate_id=cid, confidence=confidence, variance=variance,
                     objectives={"task_correctness": correctness, "total_token_cost": cost,
                                 "latency_p95": latency, "holdout_generalization_score": correctness - 0.05})


def test_dominance_requires_better_or_equal_on_all_and_strictly_on_one():
    strong = candidate("a", correctness=0.9, cost=100)
    weak = candidate("b", correctness=0.8, cost=200)
    assert SELECTOR.dominates(strong, weak) is True
    assert SELECTOR.dominates(weak, strong) is False
    equal = candidate("c", correctness=0.9, cost=100)
    assert SELECTOR.dominates(strong, equal) is False


def test_front_keeps_all_non_dominated_candidates():
    accurate = candidate("accurate", correctness=0.95, cost=500)
    cheap = candidate("cheap", correctness=0.80, cost=50)
    dominated = candidate("dominated", correctness=0.70, cost=600)
    front = SELECTOR.front([accurate, cheap, dominated])
    assert {c.candidate_id for c in front} == {"accurate", "cheap"}


def test_profiles_select_from_the_front_without_destroying_it():
    accurate = candidate("accurate", correctness=0.95, cost=500)
    cheap = candidate("cheap", correctness=0.80, cost=50)
    candidates = [accurate, cheap]
    assert SELECTOR.select(candidates, profile="quality").candidate_id == "accurate"
    assert SELECTOR.select(candidates, profile="cheap").candidate_id == "cheap"
    assert len(SELECTOR.front(candidates)) == 2  # selection did not mutate the front


def test_low_latency_profile_prefers_faster_candidate():
    slow = candidate("slow", correctness=0.9, cost=100, latency=900)
    fast = candidate("fast", correctness=0.9, cost=100, latency=120)
    assert SELECTOR.select([slow, fast], profile="low_latency").candidate_id == "fast"


def test_confidence_and_variance_break_ties_toward_stable_candidates():
    lucky = candidate("lucky", correctness=0.9, cost=100, confidence=0.4, variance=0.3)
    stable = candidate("stable", correctness=0.9, cost=100, confidence=1.0, variance=0.0)
    assert SELECTOR.select([lucky, stable], profile="balanced").candidate_id == "stable"


def test_invalid_profile_and_objectives_are_rejected():
    with pytest.raises(ValueError, match="pareto_profile_invalid"):
        SELECTOR.select([candidate("a", correctness=0.9, cost=1)], profile="magic")
    with pytest.raises(ValueError, match="pareto_objectives_required"):
        ParetoSelector(maximize=[], minimize=[])
    with pytest.raises(ValueError, match="pareto_objective_direction_conflict"):
        ParetoSelector(maximize=["x"], minimize=["x"])
    with pytest.raises(ValueError, match="candidate_objectives_invalid"):
        Candidate(candidate_id="x", objectives={})


def test_empty_population_selects_nothing():
    assert SELECTOR.select([]) is None
