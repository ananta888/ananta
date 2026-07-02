"""
Tests for agent/services/bayesian_benchmark_estimator.py.

All tests are pure unit tests — no network, no LLM calls, no filesystem.

Covers BAYES-012 (zero/low samples), BAYES-013 (posterior updates),
BAYES-014 (cumulative attempt estimates), and uncertainty labelling.
"""
import math
import pytest

from agent.services.bayesian_benchmark_estimator import (
    LOW_CONFIDENCE_DEFAULT,
    HIGH_CONFIDENCE_DEFAULT,
    HIGH_VARIANCE_THRESHOLD,
    BayesianEvidence,
    BetaBinomialPosterior,
    CumulativeSuccessEstimate,
    UncertaintyLabel,
    compute_posterior,
    estimate_bayesian_for_samples,
    estimate_cumulative_success,
    label_uncertainty,
    normalize_sample_to_evidence,
    _attempts_for_cumulative_target,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_sample(success: bool, quality_passed: bool | None = None, **kwargs) -> dict:
    s = {"ts": 1700000000, "success": success, "latency_ms": 500, "tokens_total": 100, "cost_units": 0.001}
    if quality_passed is not None:
        s["quality_passed"] = quality_passed
    s.update(kwargs)
    return s


def _make_evidence(success: bool, quality_passed: bool | None = None) -> BayesianEvidence:
    return normalize_sample_to_evidence(_make_sample(success, quality_passed))


def _success_samples(n: int) -> list[dict]:
    return [_make_sample(True, True) for _ in range(n)]


def _failure_samples(n: int) -> list[dict]:
    return [_make_sample(False, False) for _ in range(n)]


# ── normalize_sample_to_evidence ───────────────────────────────────────────────

class TestNormalizeSampleToEvidence:
    def test_success_fields_mapped(self):
        ev = normalize_sample_to_evidence(_make_sample(True, True, role_name="planner", task_kind="planning"))
        assert ev["success"] is True
        assert ev["quality_passed"] is True
        assert ev["deterministic_signal"] is True
        assert ev["role_name"] == "planner"
        assert ev["task_kind"] == "planning"

    def test_quality_passed_none_when_absent(self):
        ev = normalize_sample_to_evidence(_make_sample(True))
        assert ev["quality_passed"] is None
        assert ev["deterministic_signal"] is True  # falls back to success

    def test_quality_passed_false_beats_success_true(self):
        ev = normalize_sample_to_evidence(_make_sample(True, False))
        assert ev["success"] is True
        assert ev["quality_passed"] is False
        assert ev["deterministic_signal"] is False

    def test_malformed_input_does_not_raise(self):
        for bad in [None, 42, "string", [], object()]:
            ev = normalize_sample_to_evidence(bad)  # type: ignore[arg-type]
            assert isinstance(ev, dict)

    def test_negative_latency_clamped_to_zero(self):
        ev = normalize_sample_to_evidence({"latency_ms": -100, "success": True})
        assert ev["latency_ms"] == 0

    def test_parameters_preserved(self):
        sample = _make_sample(True)
        sample["parameters"] = {"temperature": 0.7, "top_k": 40}
        ev = normalize_sample_to_evidence(sample)
        assert ev["parameters"] == {"temperature": 0.7, "top_k": 40}

    def test_missing_parameters_yields_none(self):
        ev = normalize_sample_to_evidence(_make_sample(True))
        assert ev["parameters"] is None

    def test_schema_version_set(self):
        ev = normalize_sample_to_evidence(_make_sample(True))
        assert ev["schema_version"] == "1.0"

    def test_source_provider_model_from_caller(self):
        ev = normalize_sample_to_evidence(_make_sample(True), source="hub", provider="lmstudio", model="llama3")
        assert ev["source"] == "hub"
        assert ev["provider"] == "lmstudio"
        assert ev["model"] == "llama3"


# ── compute_posterior — zero evidence (BAYES-012) ─────────────────────────────

class TestComputePosteriorZeroEvidence:
    def test_zero_evidence_returns_prior_mean(self):
        p = compute_posterior(evidence=[], signal_key="success")
        assert p["sample_count"] == 0
        assert p["success_count"] == 0
        assert p["failure_count"] == 0
        # Prior Beta(1,1) → mean = 0.5
        assert math.isclose(p["posterior_mean"], 0.5, abs_tol=1e-6)

    def test_zero_evidence_mean_not_zero_or_one(self):
        for sig in ("success", "quality_passed", "deterministic_signal"):
            p = compute_posterior(evidence=[], signal_key=sig)
            assert 0.0 < p["posterior_mean"] < 1.0, f"signal={sig}: mean={p['posterior_mean']}"

    def test_zero_evidence_posterior_std_is_finite(self):
        p = compute_posterior(evidence=[])
        assert math.isfinite(p["posterior_std"])
        assert p["posterior_std"] > 0

    def test_zero_evidence_no_key_error(self):
        p = compute_posterior(evidence=[])
        required = {
            "alpha_prior", "beta_prior", "success_count", "failure_count",
            "posterior_alpha", "posterior_beta", "posterior_mean",
            "posterior_variance", "posterior_std", "credible_interval_90",
            "sample_count", "explanation",
        }
        assert required.issubset(p.keys())

    def test_quality_passed_none_skipped_not_failure(self):
        evidence = [_make_evidence(True, None), _make_evidence(False, None)]
        p = compute_posterior(evidence=evidence, signal_key="quality_passed")
        assert p["sample_count"] == 0
        assert math.isclose(p["posterior_mean"], 0.5, abs_tol=1e-6)


# ── compute_posterior — posterior updates (BAYES-013) ────────────────────────

class TestComputePosteriorUpdates:
    def test_one_success_raises_posterior_above_prior(self):
        prior = compute_posterior(evidence=[])
        evidence = [_make_evidence(True, True)]
        post = compute_posterior(evidence=evidence, signal_key="quality_passed")
        assert post["posterior_mean"] > prior["posterior_mean"]

    def test_one_failure_lowers_posterior_below_prior(self):
        prior = compute_posterior(evidence=[])
        evidence = [_make_evidence(False, False)]
        post = compute_posterior(evidence=evidence, signal_key="quality_passed")
        assert post["posterior_mean"] < prior["posterior_mean"]

    def test_all_successes_pushes_mean_high(self):
        ev = [_make_evidence(True, True) for _ in range(20)]
        p = compute_posterior(evidence=ev, signal_key="quality_passed")
        assert p["posterior_mean"] > 0.85

    def test_all_failures_pushes_mean_low(self):
        ev = [_make_evidence(False, False) for _ in range(20)]
        p = compute_posterior(evidence=ev, signal_key="quality_passed")
        assert p["posterior_mean"] < 0.15

    def test_mixed_evidence_between_extremes(self):
        all_success = [_make_evidence(True, True) for _ in range(10)]
        all_failure = [_make_evidence(False, False) for _ in range(10)]
        mixed = [_make_evidence(i % 2 == 0, i % 2 == 0) for i in range(10)]

        p_success = compute_posterior(evidence=all_success, signal_key="quality_passed")
        p_failure = compute_posterior(evidence=all_failure, signal_key="quality_passed")
        p_mixed = compute_posterior(evidence=mixed, signal_key="quality_passed")

        assert p_failure["posterior_mean"] < p_mixed["posterior_mean"] < p_success["posterior_mean"]

    def test_quality_passed_preferred_over_success_when_conflict(self):
        # success=True but quality_passed=False → quality signal should dominate
        ev_quality = [_make_evidence(True, False) for _ in range(10)]
        ev_success = [_make_evidence(True, None) for _ in range(10)]  # no quality data

        p_quality = compute_posterior(evidence=ev_quality, signal_key="quality_passed")
        p_success_only = compute_posterior(evidence=ev_success, signal_key="success")

        # quality_passed=False → low mean; success=True → high mean
        assert p_quality["posterior_mean"] < 0.3
        assert p_success_only["posterior_mean"] > 0.7

    def test_sample_count_matches_evidence_counted(self):
        ev = [_make_evidence(True, True) for _ in range(7)]
        p = compute_posterior(evidence=ev, signal_key="quality_passed")
        assert p["sample_count"] == 7
        assert p["success_count"] == 7
        assert p["failure_count"] == 0

    def test_custom_prior_affects_posterior(self):
        ev = [_make_evidence(True, True)]
        p_uniform = compute_posterior(evidence=ev, alpha_prior=1.0, beta_prior=1.0, signal_key="quality_passed")
        p_sceptical = compute_posterior(evidence=ev, alpha_prior=1.0, beta_prior=10.0, signal_key="quality_passed")
        # Stronger negative prior → lower posterior mean
        assert p_sceptical["posterior_mean"] < p_uniform["posterior_mean"]

    def test_no_division_by_zero_on_empty_input(self):
        p = compute_posterior(evidence=[None, {}, "bad"])  # type: ignore[list-item]
        assert math.isfinite(p["posterior_mean"])


# ── label_uncertainty ──────────────────────────────────────────────────────────

class TestLabelUncertainty:
    def _posterior_with_n(self, n: int, successes: int = None) -> BetaBinomialPosterior:
        if successes is None:
            successes = n // 2
        ev = (
            [_make_evidence(True, True) for _ in range(successes)]
            + [_make_evidence(False, False) for _ in range(n - successes)]
        )
        return compute_posterior(evidence=ev, signal_key="quality_passed")

    def test_no_evidence(self):
        p = compute_posterior(evidence=[])
        u = label_uncertainty(posterior=p)
        assert u["label"] == "no_evidence"
        assert u["low_confidence"] is True
        assert "prior_only" in u["warning_flags"]

    def test_very_low_sample_count(self):
        p = self._posterior_with_n(2)
        u = label_uncertainty(posterior=p)
        assert u["label"] == "very_low"
        assert u["low_confidence"] is True

    def test_medium_sample_count(self):
        p = self._posterior_with_n(10, successes=8)
        u = label_uncertainty(posterior=p)
        assert u["label"] in ("medium", "low")
        assert u["confidence_level"] == "medium"

    def test_high_confidence_requires_sufficient_samples_and_low_variance(self):
        p = self._posterior_with_n(50, successes=45)
        u = label_uncertainty(posterior=p)
        assert u["label"] == "high"
        assert u["low_confidence"] is False

    def test_sufficient_samples_but_balanced_split(self):
        # 30 samples at 50/50 → posterior variance is actually low (Beta(16,16))
        # so label is "high", which is correct — model is reliably mediocre
        p = self._posterior_with_n(30, successes=15)
        u = label_uncertainty(posterior=p)
        # with 30 samples, count ≥ high_threshold → medium or high
        assert u["label"] in ("medium", "high")

    def test_required_keys_present(self):
        p = compute_posterior(evidence=[])
        u = label_uncertainty(posterior=p)
        for key in ("label", "confidence_level", "sample_count", "posterior_variance", "low_confidence", "warning_flags"):
            assert key in u


# ── estimate_cumulative_success (BAYES-014) ───────────────────────────────────

class TestEstimateCumulativeSuccess:
    def _posterior(self, mean: float, n: int = 20) -> BetaBinomialPosterior:
        successes = round(mean * n)
        ev = (
            [_make_evidence(True, True) for _ in range(successes)]
            + [_make_evidence(False, False) for _ in range(n - successes)]
        )
        return compute_posterior(evidence=ev, signal_key="quality_passed")

    def test_zero_attempts_returns_zero_probability(self):
        p = self._posterior(0.7)
        est = estimate_cumulative_success(posterior=p, n_attempts=0)
        assert est["cumulative_probability"] == 0.0
        assert "zero_attempts" in est["warning_flags"]

    def test_n_1_equals_per_attempt_probability(self):
        p = self._posterior(0.7)
        est = estimate_cumulative_success(posterior=p, n_attempts=1, mode="independent")
        assert math.isclose(
            est["cumulative_probability"], est["per_attempt_probability"], rel_tol=1e-4
        )

    def test_cumulative_increases_monotonically_neutral(self):
        p = self._posterior(0.4)
        probs = [
            estimate_cumulative_success(posterior=p, n_attempts=n, mode="independent")["cumulative_probability"]
            for n in range(1, 11)
        ]
        for i in range(len(probs) - 1):
            assert probs[i] <= probs[i + 1], f"not monotonic at n={i+1}"

    def test_pessimistic_leq_neutral_for_n_gt_1(self):
        p = self._posterior(0.5)
        for n in [2, 5, 10, 20]:
            neutral = estimate_cumulative_success(posterior=p, n_attempts=n, mode="independent")["cumulative_probability"]
            pessimistic = estimate_cumulative_success(posterior=p, n_attempts=n, mode="pessimistic")["cumulative_probability"]
            assert pessimistic <= neutral + 1e-9, f"pessimistic > neutral at n={n}"

    def test_optimistic_never_exceeds_cap(self):
        p = self._posterior(0.9)
        for n in [1, 5, 20, 100]:
            est = estimate_cumulative_success(posterior=p, n_attempts=n, mode="optimistic", max_cap=0.9999)
            assert est["cumulative_probability"] <= 0.9999 + 1e-9

    def test_n_5(self):
        p = self._posterior(0.5)
        est = estimate_cumulative_success(posterior=p, n_attempts=5)
        # P(≥1 in 5 with p≈0.5) ≈ 0.969
        assert est["cumulative_probability"] > 0.9

    def test_n_20_high_success_probability(self):
        p = self._posterior(0.6)
        est = estimate_cumulative_success(posterior=p, n_attempts=20)
        assert est["cumulative_probability"] > 0.99

    def test_negative_n_raises(self):
        p = self._posterior(0.5)
        with pytest.raises(ValueError):
            estimate_cumulative_success(posterior=p, n_attempts=-1)

    def test_zero_probability_model(self):
        # Beta(1, 31): mean ≈ 0.031, P(≥1 in 5) ≈ 0.147 — threshold is < 0.2
        p = compute_posterior(evidence=[_make_evidence(False, False)] * 30, signal_key="quality_passed")
        est = estimate_cumulative_success(posterior=p, n_attempts=5)
        assert est["cumulative_probability"] < 0.2

    def test_mode_field_recorded(self):
        p = self._posterior(0.5)
        for mode in ("independent", "pessimistic", "optimistic"):
            est = estimate_cumulative_success(posterior=p, n_attempts=3, mode=mode)
            assert est["mode"] == mode


# ── _attempts_for_cumulative_target ───────────────────────────────────────────

class TestAttemptsForTarget:
    def test_impossible_when_p_zero(self):
        assert _attempts_for_cumulative_target(0.0, 0.5) is None

    def test_one_attempt_when_p_gte_target(self):
        assert _attempts_for_cumulative_target(0.9, 0.5) == 1

    def test_reasonable_attempts_for_p05(self):
        # p=0.5: need ceil(log(0.5)/log(0.5)) = 1 for 50%
        # need ceil(log(0.2)/log(0.5)) = 3 for 80%
        assert _attempts_for_cumulative_target(0.5, 0.5) == 1
        assert _attempts_for_cumulative_target(0.5, 0.80) == 3

    def test_caps_at_10000(self):
        result = _attempts_for_cumulative_target(0.0001, 0.9999)
        assert result is not None
        assert result <= 10_000

    def test_all_targets_met(self):
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            for target in [0.5, 0.8, 0.95]:
                n = _attempts_for_cumulative_target(p, target)
                if n is not None:
                    cumulative = 1.0 - (1.0 - p) ** n
                    assert cumulative >= target - 1e-9, f"p={p}, target={target}, n={n}, cum={cumulative}"


# ── estimate_bayesian_for_samples ─────────────────────────────────────────────

class TestEstimateBayesianForSamples:
    def test_empty_samples_prior_only(self):
        result = estimate_bayesian_for_samples([])
        assert result["estimate_status"] == "prior_only"
        assert result["evidence_count"] == 0
        # Prior mean 0.5, not 0.0 or 1.0
        assert 0.0 < result["posterior_success_probability"] < 1.0

    def test_all_success_high_posterior(self):
        samples = _success_samples(15)
        result = estimate_bayesian_for_samples(samples)
        assert result["posterior_success_probability"] > 0.85
        assert result["posterior_quality_probability"] is not None
        assert result["posterior_quality_probability"] > 0.85

    def test_all_failure_low_posterior(self):
        samples = _failure_samples(15)
        result = estimate_bayesian_for_samples(samples)
        assert result["posterior_success_probability"] < 0.15
        assert result["posterior_quality_probability"] < 0.15

    def test_quality_preferred_as_primary_signal(self):
        samples = _success_samples(10)
        result = estimate_bayesian_for_samples(samples)
        assert result["primary_signal"] == "quality_passed"

    def test_success_fallback_when_no_quality(self):
        samples = [_make_sample(True) for _ in range(10)]  # no quality_passed
        result = estimate_bayesian_for_samples(samples)
        assert result["primary_signal"] == "success"
        assert result["posterior_quality_probability"] is None

    def test_low_confidence_flag_for_few_samples(self):
        samples = _success_samples(2)
        result = estimate_bayesian_for_samples(samples)
        assert result["low_confidence"] is True

    def test_high_confidence_flag_for_many_consistent_samples(self):
        samples = _success_samples(30)
        result = estimate_bayesian_for_samples(samples)
        assert result["low_confidence"] is False

    def test_attempt_estimates_present(self):
        samples = _success_samples(10)
        result = estimate_bayesian_for_samples(samples, include_attempt_estimates=True)
        assert "estimated_attempts_for_50_percent" in result
        assert "estimated_attempts_for_80_percent" in result
        assert "estimated_attempts_for_95_percent" in result

    def test_attempt_estimates_absent_when_disabled(self):
        result = estimate_bayesian_for_samples(_success_samples(5), include_attempt_estimates=False)
        assert "estimated_attempts_for_50_percent" not in result

    def test_attempt_estimates_ordered(self):
        samples = _success_samples(10)
        result = estimate_bayesian_for_samples(samples)
        a50 = result.get("estimated_attempts_for_50_percent") or 0
        a80 = result.get("estimated_attempts_for_80_percent") or 0
        a95 = result.get("estimated_attempts_for_95_percent") or 0
        assert a50 <= a80 <= a95

    def test_malformed_samples_skipped(self):
        samples = [None, "bad", 42, {}, _make_sample(True, True)]  # type: ignore[list-item]
        result = estimate_bayesian_for_samples(samples)
        assert result["evidence_count"] >= 1  # at least the valid sample counted

    def test_required_keys_present(self):
        result = estimate_bayesian_for_samples([])
        for key in (
            "posterior_success_probability",
            "posterior_quality_probability",
            "primary_signal",
            "evidence_count",
            "success_count",
            "failure_count",
            "credible_interval_90",
            "uncertainty",
            "low_confidence",
            "estimate_status",
        ):
            assert key in result, f"missing key: {key}"

    def test_credible_interval_ordered(self):
        result = estimate_bayesian_for_samples(_success_samples(20))
        lo, hi = result["credible_interval_90"]
        assert lo <= hi
        assert 0.0 <= lo <= 1.0
        assert 0.0 <= hi <= 1.0
