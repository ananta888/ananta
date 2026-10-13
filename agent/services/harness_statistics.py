"""Paired repeat-run robustness using the existing Bayesian estimator (SEH-016).

The credible interval describes the probability of a practical paired win,
not a frequentist confidence interval for the continuous metric mean.
These observations are analytics, never evidence identities or release grants.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent.services.bayesian_benchmark_estimator import compute_posterior
from agent.services.harness_gates import GateReport
from agent.services.harness_pareto import Candidate
from agent.services.harness_promotion import PromotionController, PromotionDecision
from ananta_contracts.harness_metrics import finite_number, freeze_metrics


@dataclass(frozen=True, slots=True)
class HarnessRepeatSample:
    seed: int
    metrics: Mapping[str, float]

    def __post_init__(self):
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("harness_repeat_seed_invalid")
        object.__setattr__(self, "metrics", freeze_metrics(self.metrics))


@dataclass(frozen=True, slots=True)
class HarnessNoisePolicy:
    minimum_runs: int = 5
    metric: str = "task_correctness"
    maximize: bool = True
    practical_delta: float = 0.02
    minimum_win_probability: float = 0.5

    def __post_init__(self):
        if (
            type(self.minimum_runs) is not int
            or not 5 <= self.minimum_runs <= 1000
            or not isinstance(self.metric, str)
            or not self.metric
            or type(self.maximize) is not bool
            or not finite_number(self.practical_delta)
            or self.practical_delta < 0
            or not finite_number(self.minimum_win_probability)
            or not 0.5 <= self.minimum_win_probability < 1
        ):
            raise ValueError("harness_noise_policy_invalid")


def assess_repeats(
    *, candidate: Sequence[HarnessRepeatSample], baseline: Sequence[HarnessRepeatSample], policy: HarnessNoisePolicy
) -> dict:
    candidate_by_seed = {sample.seed: sample for sample in candidate}
    baseline_by_seed = {sample.seed: sample for sample in baseline}
    if (
        len(candidate_by_seed) != len(candidate)
        or len(baseline_by_seed) != len(baseline)
        or candidate_by_seed.keys() != baseline_by_seed.keys()
    ):
        raise ValueError("harness_repeat_seed_pairs_invalid")
    if len(candidate) > 1000:
        raise ValueError("harness_repeat_samples_unbounded")
    deltas, candidate_values, baseline_values = [], [], []
    for seed in sorted(candidate_by_seed):
        left, right = candidate_by_seed[seed].metrics, baseline_by_seed[seed].metrics
        if policy.metric not in left or policy.metric not in right:
            raise ValueError("harness_repeat_metric_missing")
        a, b = left[policy.metric], right[policy.metric]
        candidate_values.append(a)
        baseline_values.append(b)
        delta = a - b if policy.maximize else b - a
        if not finite_number(delta):
            raise ValueError("harness_repeat_numeric_overflow")
        deltas.append(delta)
    posterior = compute_posterior(evidence=[{"quality_passed": delta > policy.practical_delta} for delta in deltas])
    lower, upper = posterior["credible_interval_90"]
    count = len(deltas)
    allowed = count >= policy.minimum_runs and lower > policy.minimum_win_probability
    result = {
        "method": "paired_practical_wins_beta_binomial_90_normal_approx",
        "metric": policy.metric,
        "sample_count": count,
        "minimum_runs": policy.minimum_runs,
        "practical_delta": policy.practical_delta,
        "candidate_mean": statistics.fmean(candidate_values) if count else 0.0,
        "baseline_mean": statistics.fmean(baseline_values) if count else 0.0,
        "mean_delta": statistics.fmean(deltas) if count else 0.0,
        "delta_variance": statistics.variance(deltas) if count > 1 else 0.0,
        "candidate_variance": statistics.variance(candidate_values) if count > 1 else 0.0,
        "baseline_variance": statistics.variance(baseline_values) if count > 1 else 0.0,
        "win_probability": posterior["posterior_mean"],
        "win_rate_credible_interval_90": [lower, upper],
        "robust_improvement": allowed,
        "reason": "ok" if allowed else "insufficient_runs" if count < policy.minimum_runs else "within_noise_band",
    }
    freeze_metrics(
        {
            key: result[key]
            for key in (
                "candidate_mean",
                "baseline_mean",
                "mean_delta",
                "delta_variance",
                "candidate_variance",
                "baseline_variance",
            )
        }
    )
    return result


def record_repeat_assessment(store, *, experiment_id: str, assessment: Mapping) -> str:
    """Store numeric variance/CI analytics in the existing append-only run store."""
    interval = assessment["win_rate_credible_interval_90"]
    metrics = {
        f"repeat_{key}": assessment[key]
        for key in (
            "sample_count",
            "mean_delta",
            "delta_variance",
            "candidate_mean",
            "candidate_variance",
            "baseline_mean",
            "baseline_variance",
            "win_probability",
        )
    }
    metrics.update(repeat_win_ci_lower=interval[0], repeat_win_ci_upper=interval[1])
    return store.record_benchmark_run(experiment_id=experiment_id, metrics=dict(freeze_metrics(metrics)))


class NoiseAwarePromotionController:
    """Composition protects LSP: strict repeat requirements are an additive API.

    Returns a decision proposal only. The Hub release authority must still
    verify registered production evidence and authorize activation separately.
    """

    def __init__(self, controller: PromotionController, *, policy: HarnessNoisePolicy = HarnessNoisePolicy()):
        self._controller, self._policy = controller, policy

    def decide(
        self,
        *,
        candidate_id: str,
        baseline_id: str,
        candidate: Sequence[HarnessRepeatSample],
        baseline: Sequence[HarnessRepeatSample],
        gate_report: GateReport | None,
    ) -> tuple[PromotionDecision, dict]:
        assessment = assess_repeats(candidate=candidate, baseline=baseline, policy=self._policy)
        if not assessment["robust_improvement"]:
            return PromotionDecision(candidate_id, baseline_id, "reject", assessment["reason"]), assessment
        c = Candidate(candidate_id, _mean_metrics(candidate))
        b = Candidate(baseline_id, _mean_metrics(baseline))
        return self._controller.decide(candidate=c, baseline=b, gate_report=gate_report), assessment


def _mean_metrics(samples):
    keys = set(samples[0].metrics)
    if any(set(sample.metrics) != keys for sample in samples):
        raise ValueError("harness_repeat_metric_schema_mismatch")
    return {key: statistics.fmean(sample.metrics[key] for sample in samples) for key in sorted(keys)}
