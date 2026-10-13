"""Repeat thresholds, practical noise, pairing and immutable finite metrics."""

from dataclasses import replace

import pytest

from agent.services.harness_benchmark import BaselineRecord, BenchmarkRegistry
from agent.services.harness_gates import GateEngine, HarnessGate, regression_gate, threshold_gate
from agent.services.harness_pareto import Candidate, ParetoSelector
from agent.services.harness_promotion import PromotionController
from agent.services.harness_statistics import (
    HarnessNoisePolicy,
    HarnessRepeatSample,
    NoiseAwarePromotionController,
    assess_repeats,
    record_repeat_assessment,
)
from tests.harness import test_harness_evolution_store as store_helpers

store = store_helpers.store


def samples(values, cost=10):
    return [
        HarnessRepeatSample(seed, {"task_correctness": value, "total_token_cost": cost})
        for seed, value in enumerate(values)
    ]


def test_minimum_runs_and_noise_band_prevent_lucky_or_tiny_promotions():
    policy = HarnessNoisePolicy()
    assert (
        assess_repeats(candidate=samples([0.9]), baseline=samples([0.7]), policy=policy)["reason"]
        == "insufficient_runs"
    )
    result = assess_repeats(candidate=samples([0.801] * 5), baseline=samples([0.8] * 5), policy=policy)
    assert not result["robust_improvement"] and result["reason"] == "within_noise_band"


def test_stable_paired_improvement_has_variance_and_credible_interval():
    result = assess_repeats(candidate=samples([0.9] * 5), baseline=samples([0.7] * 5), policy=HarnessNoisePolicy())
    assert result["robust_improvement"] and result["delta_variance"] == 0
    assert result["mean_delta"] == pytest.approx(0.2)
    assert result["win_rate_credible_interval_90"][0] > 0.5
    assert result["method"].endswith("normal_approx")


def test_variable_results_with_positive_mean_do_not_pass_stability_check():
    result = assess_repeats(
        candidate=samples([0.6, 1.0, 0.6, 1.0, 0.6]), baseline=samples([0.7] * 5), policy=HarnessNoisePolicy()
    )
    assert result["mean_delta"] > 0 and result["delta_variance"] > 0
    assert not result["robust_improvement"]


def test_missing_or_duplicate_seed_pairs_fail_closed():
    candidate, baseline = samples([0.9] * 5), samples([0.7] * 5)
    with pytest.raises(ValueError, match="harness_repeat_seed_pairs_invalid"):
        assess_repeats(candidate=candidate[:-1], baseline=baseline, policy=HarnessNoisePolicy())
    candidate[1] = replace(candidate[1], seed=0)
    with pytest.raises(ValueError, match="harness_repeat_seed_pairs_invalid"):
        assess_repeats(candidate=candidate, baseline=baseline, policy=HarnessNoisePolicy())


def test_minimized_resource_objective_can_be_the_practical_win_signal():
    result = assess_repeats(
        candidate=samples([0.8] * 5, cost=5),
        baseline=samples([0.8] * 5, cost=10),
        policy=HarnessNoisePolicy(metric="total_token_cost", maximize=False, practical_delta=1),
    )
    assert result["robust_improvement"]


def test_noise_controller_keeps_hard_gates_and_uses_repeat_means():
    controller = NoiseAwarePromotionController(
        PromotionController(ParetoSelector(maximize=("task_correctness",), minimize=("total_token_cost",)))
    )
    candidate, baseline = samples([0.9] * 5, cost=5), samples([0.7] * 5, cost=10)
    report = GateEngine([threshold_gate("quality", "task_correctness", 0.8)]).evaluate({"task_correctness": 0.9})
    decision, assessment = controller.decide(
        candidate_id="candidate", baseline_id="baseline", candidate=candidate, baseline=baseline, gate_report=report
    )
    assert decision.action == "promote" and assessment["robust_improvement"]
    decision, _ = controller.decide(
        candidate_id="candidate", baseline_id="baseline", candidate=candidate, baseline=baseline, gate_report=None
    )
    assert decision.action == "reject"


def test_analytics_are_recorded_in_existing_benchmark_store():
    recorded = []

    class StoreDouble:
        def record_benchmark_run(self, **values):
            recorded.append(values)
            return "technical-benchmark-row"

    assessment = assess_repeats(candidate=samples([0.9] * 5), baseline=samples([0.7] * 5), policy=HarnessNoisePolicy())
    assert (
        record_repeat_assessment(StoreDouble(), experiment_id="experiment", assessment=assessment)
        == "technical-benchmark-row"
    )
    assert recorded[0]["metrics"]["repeat_win_ci_lower"] > 0.5
    assert recorded[0]["metrics"]["repeat_delta_variance"] == 0


def test_repeat_variance_and_interval_are_persisted_in_sql_store(store):
    genome = store_helpers.genome()
    store.append_genome(genome)
    experiment = store.start_experiment(
        genome_id=genome.genome_id,
        task="synthetic-statistics",
        model="synthetic",
        dataset_version="synthetic-v1",
        code_commit="1" * 40,
        runtime_id="test-runtime",
    )
    assessment = assess_repeats(candidate=samples([0.9] * 5), baseline=samples([0.7] * 5), policy=HarnessNoisePolicy())
    row_id = record_repeat_assessment(store, experiment_id=experiment, assessment=assessment)
    rows = store.benchmark_runs(experiment)
    assert rows[0]["run_id"] == row_id
    assert rows[0]["metrics"]["repeat_candidate_variance"] == 0
    assert rows[0]["metrics"]["repeat_win_ci_lower"] > 0.5
    # A benchmark row key is a technical storage ID, not Registry evidence.
    assert not row_id.startswith(("SRC_", "RUN_"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 10**1000])
def test_nonfinite_or_boolean_values_are_rejected_by_metrics_and_gates(value):
    with pytest.raises(ValueError):
        Candidate("invalid", {"task_correctness": value})
    with pytest.raises(ValueError):
        HarnessRepeatSample(1, {"task_correctness": value})
    report = GateEngine([regression_gate("regression", "task_correctness", 0.8, 0.1)]).evaluate(
        {"task_correctness": value}
    )
    assert report.disqualified


def test_mutable_input_dictionaries_cannot_change_candidates_or_baselines():
    metrics = {"task_correctness": 0.9}
    candidate = Candidate("candidate", metrics)
    baseline = BaselineRecord("model", "harness", metrics, "suite")
    metrics["task_correctness"] = 0.1
    assert candidate.objectives["task_correctness"] == baseline.metrics["task_correctness"] == 0.9
    registry = BenchmarkRegistry()
    registry.record_baseline(baseline)
    with pytest.raises(ValueError, match="benchmark_baseline_conflict"):
        registry.record_baseline(replace(baseline, confidence=0.5))


def test_single_candidate_must_still_have_all_objectives_and_ids_are_unique():
    selector = ParetoSelector(maximize=("task_correctness",), minimize=("total_token_cost",))
    with pytest.raises(ValueError, match="pareto_metric_missing"):
        selector.front([Candidate("missing", {"task_correctness": 0.9})])
    candidate = Candidate("duplicate", {"task_correctness": 0.9, "total_token_cost": 1})
    with pytest.raises(ValueError, match="pareto_candidate_ids_duplicate"):
        selector.front([candidate, candidate])


def test_a_truthy_non_boolean_gate_result_is_not_a_security_pass():
    assert GateEngine([HarnessGate("invalid", True, lambda _: ("yes", "ok"))]).evaluate({}).disqualified


def test_paired_arithmetic_overflow_cannot_be_a_robust_win():
    with pytest.raises(ValueError, match="harness_repeat_numeric_overflow"):
        assess_repeats(candidate=samples([1e308] * 5), baseline=samples([-1e308] * 5), policy=HarnessNoisePolicy())


def test_mutating_the_registration_list_does_not_remove_hard_gates():
    gates = [threshold_gate("quality", "task_correctness", 0.8)]
    engine = GateEngine(gates)
    gates.clear()
    assert engine.evaluate({"task_correctness": 0.5}).disqualified
