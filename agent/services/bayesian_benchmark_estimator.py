"""
Pure Bayesian estimator for benchmark evidence.

Derives posterior success/quality probabilities from existing benchmark
samples using a beta-binomial model. No network calls, no LLM calls,
no benchmark execution side effects.

Evidence hierarchy (strongest to weakest):
  quality_passed  — deterministic quality-gate evaluation
  success         — raw LLM call returned non-empty response

When quality_passed is available, it is used as the primary signal.
"""
from __future__ import annotations

import math
from typing import Any, Literal

# ── Evidence record ────────────────────────────────────────────────────────────

EVIDENCE_SCHEMA_VERSION = "1.0"

_SENTINEL = object()


class BayesianEvidence(dict):
    """
    Normalised per-sample evidence record derived from any benchmark system.

    Acts as a plain dict so it is JSON-serialisable without extra steps.
    """


class BetaBinomialPosterior(dict):
    """Beta-binomial posterior over a binary benchmark signal (plain dict)."""


class CumulativeSuccessEstimate(dict):
    """P(at least one success after N attempts) estimate (plain dict)."""


class UncertaintyLabel(dict):
    """Human-readable uncertainty assessment of a posterior (plain dict)."""


# ── Evidence normalisation ─────────────────────────────────────────────────────

def normalize_sample_to_evidence(
    sample: dict[str, Any],
    *,
    source: str = "unknown",
    provider: str | None = None,
    model: str = "",
) -> BayesianEvidence:
    """
    Convert a raw benchmark sample dict into a BayesianEvidence record.

    Handles missing/malformed fields defensively; never raises on bad input.
    Fields present in the sample are preferred over caller-supplied defaults.
    """
    if not isinstance(sample, dict):
        sample = {}

    success = bool(sample.get("success", False))
    raw_qp = sample.get("quality_passed", _SENTINEL)
    quality_passed: bool | None = (
        bool(raw_qp) if raw_qp is not _SENTINEL and raw_qp is not None else None
    )
    deterministic_signal = quality_passed if quality_passed is not None else success

    def _int(val: Any, default: int = 0) -> int:
        try:
            return max(0, int(val or 0))
        except (TypeError, ValueError):
            return default

    def _float(val: Any, default: float = 0.0) -> float:
        try:
            return max(0.0, float(val or 0.0))
        except (TypeError, ValueError):
            return default

    raw_params = sample.get("parameters")
    parameters: dict[str, Any] | None = dict(raw_params) if isinstance(raw_params, dict) else None

    ev = BayesianEvidence()
    ev["schema_version"] = EVIDENCE_SCHEMA_VERSION
    ev["source"] = str(source or "unknown")
    ev["provider"] = str(provider).strip() if provider else sample.get("provider") or None
    ev["model"] = str(model or sample.get("model") or "")
    ev["role_name"] = str(sample.get("role_name") or "").strip() or None
    ev["task_kind"] = str(sample.get("task_kind") or "").strip() or None
    ev["parameters"] = parameters
    ev["ts"] = _int(sample.get("ts", 0))
    ev["success"] = success
    ev["quality_passed"] = quality_passed
    ev["latency_ms"] = _int(sample.get("latency_ms"))
    ev["tokens_total"] = _int(sample.get("tokens_total"))
    ev["cost_units"] = _float(sample.get("cost_units"))
    ev["deterministic_signal"] = deterministic_signal
    return ev


# ── Beta credible interval ─────────────────────────────────────────────────────

def _beta_credible_interval_90(alpha: float, beta: float) -> tuple[float, float]:
    """
    90% credible interval for Beta(alpha, beta) via Normal approximation.

    Accurate for alpha + beta > 5. Clamped to [0, 1].
    """
    total = alpha + beta
    mean = alpha / total
    variance = (alpha * beta) / (total ** 2 * (total + 1))
    std = math.sqrt(variance) if variance > 0 else 0.0
    z = 1.6449  # 95th percentile of standard Normal → 90% two-sided CI
    lower = max(0.0, mean - z * std)
    upper = min(1.0, mean + z * std)
    return (round(lower, 6), round(upper, 6))


# ── Posterior computation ──────────────────────────────────────────────────────

def compute_posterior(
    *,
    evidence: list[BayesianEvidence],
    signal_key: Literal["quality_passed", "success", "deterministic_signal"] = "quality_passed",
    alpha_prior: float = 1.0,
    beta_prior: float = 1.0,
) -> BetaBinomialPosterior:
    """
    Compute a beta-binomial posterior from a list of evidence records.

    When signal_key is ``"quality_passed"``, records without a quality_passed
    value (None) are skipped rather than counted as failures — absence of
    quality data is treated as missing evidence, not negative evidence.

    Default priors Beta(1, 1) are uniform: zero evidence yields
    posterior_mean = 0.5, not 0.0 or 1.0.
    """
    alpha_prior = max(0.01, float(alpha_prior or 1.0))
    beta_prior = max(0.01, float(beta_prior or 1.0))

    successes = 0
    failures = 0
    skipped = 0

    for ev in evidence or []:
        if not isinstance(ev, dict):
            skipped += 1
            continue
        if signal_key == "quality_passed":
            qp = ev.get("quality_passed")
            if qp is None:
                skipped += 1
                continue
            if qp:
                successes += 1
            else:
                failures += 1
        elif signal_key == "success":
            if ev.get("success"):
                successes += 1
            else:
                failures += 1
        else:  # deterministic_signal
            if ev.get("deterministic_signal"):
                successes += 1
            else:
                failures += 1

    posterior_alpha = alpha_prior + successes
    posterior_beta = beta_prior + failures
    n = successes + failures
    total_ab = posterior_alpha + posterior_beta

    posterior_mean = posterior_alpha / total_ab
    posterior_variance = (posterior_alpha * posterior_beta) / (total_ab ** 2 * (total_ab + 1))
    posterior_std = math.sqrt(posterior_variance)
    ci_90 = _beta_credible_interval_90(posterior_alpha, posterior_beta)

    explanation: dict[str, Any] = {
        "signal_used": signal_key,
        "prior": f"Beta({alpha_prior:.2f}, {beta_prior:.2f})",
        "evidence_counted": n,
        "skipped_no_signal": skipped,
        "note": (
            "prior_only — no evidence observed yet"
            if n == 0
            else f"{successes} successes and {failures} failures observed"
        ),
    }

    p = BetaBinomialPosterior()
    p["alpha_prior"] = alpha_prior
    p["beta_prior"] = beta_prior
    p["success_count"] = successes
    p["failure_count"] = failures
    p["posterior_alpha"] = posterior_alpha
    p["posterior_beta"] = posterior_beta
    p["posterior_mean"] = round(posterior_mean, 6)
    p["posterior_variance"] = round(posterior_variance, 8)
    p["posterior_std"] = round(posterior_std, 6)
    p["credible_interval_90"] = ci_90
    p["sample_count"] = n
    p["explanation"] = explanation
    return p


# ── Uncertainty labelling ──────────────────────────────────────────────────────

LOW_CONFIDENCE_DEFAULT = 5
HIGH_CONFIDENCE_DEFAULT = 20
HIGH_VARIANCE_THRESHOLD = 0.05  # posterior variance above this → uncertain


def label_uncertainty(
    *,
    posterior: BetaBinomialPosterior,
    low_threshold: int = LOW_CONFIDENCE_DEFAULT,
    high_threshold: int = HIGH_CONFIDENCE_DEFAULT,
) -> UncertaintyLabel:
    """
    Derive a human-readable uncertainty label from a posterior.

    Confidence grades (label → confidence_level):
      no_evidence  → low    : sample_count == 0
      very_low     → low    : count < low_threshold
      low          → medium : low_threshold ≤ count < high_threshold OR high variance
      medium       → medium : count ≥ high_threshold AND high variance
      high         → high   : count ≥ high_threshold AND low variance
    """
    n = int(posterior.get("sample_count") or 0)
    var = float(posterior.get("posterior_variance") or 0.0)
    flags: list[str] = []

    if n == 0:
        label, conf, low_conf = "no_evidence", "low", True
        flags.append("prior_only")
    elif n < low_threshold:
        label, conf, low_conf = "very_low", "low", True
        flags.append(f"count_{n}_below_threshold_{low_threshold}")
    elif n < high_threshold:
        if var > HIGH_VARIANCE_THRESHOLD:
            label = "low"
            flags.append("high_variance")
        else:
            label = "medium"
        conf, low_conf = "medium", True
    else:
        if var > HIGH_VARIANCE_THRESHOLD:
            label, conf, low_conf = "medium", "medium", False
            flags.append("high_variance")
        else:
            label, conf, low_conf = "high", "high", False

    ul = UncertaintyLabel()
    ul["label"] = label
    ul["confidence_level"] = conf
    ul["sample_count"] = n
    ul["posterior_variance"] = round(var, 8)
    ul["low_confidence"] = low_conf
    ul["warning_flags"] = flags
    return ul


# ── Cumulative success estimate ────────────────────────────────────────────────

def _attempts_for_cumulative_target(p: float, target: float) -> int | None:
    """
    Minimum N ≥ 0 such that P(≥1 success in N independent attempts) ≥ target.

    Returns None when p == 0 (probability is zero, target unreachable).
    Caps result at 10 000 to avoid pathologically large values.
    """
    if p <= 0.0:
        return None
    if p >= 1.0:
        return 1
    target = min(float(target), 0.9999)
    # P(≥1 in N) = 1-(1-p)^N ≥ target  ⟺  N ≥ log(1-target)/log(1-p)
    n = math.ceil(math.log(1.0 - target) / math.log(1.0 - p))
    return max(1, min(int(n), 10_000))


def estimate_cumulative_success(
    *,
    posterior: BetaBinomialPosterior,
    n_attempts: int,
    mode: Literal["independent", "pessimistic", "optimistic"] = "independent",
    correlation_factor: float = 0.15,
    optimism_factor: float = 1.10,
    max_cap: float = 0.9999,
) -> CumulativeSuccessEstimate:
    """
    Estimate P(at least one success after n_attempts) from a posterior.

    Modes
    -----
    independent
        Standard: P = 1 - (1-p)^N.  Assumes each attempt is independent.
    pessimistic
        Applies a correlation penalty that reduces effective per-attempt
        probability as N grows, modelling correlated failure modes such as
        repeated context errors or systematic model weaknesses:
            p_eff = p / (1 + correlation_factor * (N - 1))
    optimistic
        Applies optimism_factor to p (capped at 1.0), modelling cases where
        retry strategies or prompt variation reduce failure modes.

    The result is always ≤ max_cap; never returns 1.0 for finite evidence.

    Raises ValueError for negative n_attempts.
    """
    if n_attempts < 0:
        raise ValueError(f"n_attempts must be non-negative, got {n_attempts}")

    warnings: list[str] = []
    p = float(posterior.get("posterior_mean") or 0.0)

    if posterior.get("sample_count", 0) == 0:
        warnings.append("prior_only_no_evidence")

    if n_attempts == 0:
        est = CumulativeSuccessEstimate()
        est["attempts"] = 0
        est["per_attempt_probability"] = round(p, 6)
        est["cumulative_probability"] = 0.0
        est["mode"] = mode
        est["warning_flags"] = ["zero_attempts"] + warnings
        return est

    if p <= 0.0:
        est = CumulativeSuccessEstimate()
        est["attempts"] = n_attempts
        est["per_attempt_probability"] = 0.0
        est["cumulative_probability"] = 0.0
        est["mode"] = mode
        est["warning_flags"] = ["zero_probability"] + warnings
        return est

    if mode == "pessimistic":
        p_eff = p / (1.0 + correlation_factor * max(0, n_attempts - 1))
        p_eff = max(0.0, min(1.0, p_eff))
        cumulative = 1.0 - (1.0 - p_eff) ** n_attempts
        if p < 0.1:
            warnings.append("low_per_attempt_probability")
        per_attempt_reported = p_eff
    elif mode == "optimistic":
        p_opt = min(1.0, p * optimism_factor)
        cumulative = 1.0 - (1.0 - p_opt) ** n_attempts
        if p_opt >= 0.99:
            warnings.append("optimistic_probability_near_certainty")
        per_attempt_reported = p_opt
    else:  # independent
        cumulative = 1.0 - (1.0 - p) ** n_attempts
        per_attempt_reported = p

    cumulative = min(float(cumulative), float(max_cap))

    est = CumulativeSuccessEstimate()
    est["attempts"] = n_attempts
    est["per_attempt_probability"] = round(per_attempt_reported, 6)
    est["cumulative_probability"] = round(cumulative, 6)
    est["mode"] = mode
    est["warning_flags"] = warnings
    return est


# ── Main high-level entry point ────────────────────────────────────────────────

def estimate_bayesian_for_samples(
    samples: list[dict[str, Any]],
    *,
    source: str = "unknown",
    provider: str | None = None,
    model: str = "",
    alpha_prior: float = 1.0,
    beta_prior: float = 1.0,
    low_confidence_threshold: int = LOW_CONFIDENCE_DEFAULT,
    high_confidence_threshold: int = HIGH_CONFIDENCE_DEFAULT,
    include_attempt_estimates: bool = True,
) -> dict[str, Any]:
    """
    Full Bayesian estimation pipeline from raw benchmark sample dicts.

    Returns a plain dict with advisory ``bayesian_estimate`` fields suitable
    for attaching to existing score or recommendation payloads.  Always
    backward compatible: callers that ignore unknown keys are unaffected.

    Quality-gate evidence (``quality_passed``) is preferred over raw success
    when available.  Raw success is used as fallback only when no sample has
    a quality_passed value.
    """
    evidence: list[BayesianEvidence] = []
    for s in samples or []:
        if isinstance(s, dict):
            evidence.append(
                normalize_sample_to_evidence(s, source=source, provider=provider, model=model)
            )

    quality_evidence = [e for e in evidence if e.get("quality_passed") is not None]

    success_posterior = compute_posterior(
        evidence=evidence,
        signal_key="success",
        alpha_prior=alpha_prior,
        beta_prior=beta_prior,
    )

    if quality_evidence:
        quality_posterior = compute_posterior(
            evidence=quality_evidence,
            signal_key="quality_passed",
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
        )
        primary_posterior = quality_posterior
        primary_signal = "quality_passed"
    else:
        quality_posterior = None
        primary_posterior = success_posterior
        primary_signal = "success"

    uncertainty = label_uncertainty(
        posterior=primary_posterior,
        low_threshold=low_confidence_threshold,
        high_threshold=high_confidence_threshold,
    )

    result: dict[str, Any] = {
        "posterior_success_probability": success_posterior["posterior_mean"],
        "posterior_quality_probability": (
            quality_posterior["posterior_mean"] if quality_posterior else None
        ),
        "primary_signal": primary_signal,
        "evidence_count": primary_posterior["sample_count"],
        "success_count": success_posterior["success_count"],
        "failure_count": success_posterior["failure_count"],
        "credible_interval_90": primary_posterior["credible_interval_90"],
        "uncertainty": uncertainty,
        "low_confidence": uncertainty["low_confidence"],
        "estimate_status": (
            "prior_only" if primary_posterior["sample_count"] == 0 else "active"
        ),
    }

    if include_attempt_estimates:
        p = primary_posterior["posterior_mean"]
        result["estimated_attempts_for_50_percent"] = _attempts_for_cumulative_target(p, 0.50)
        result["estimated_attempts_for_80_percent"] = _attempts_for_cumulative_target(p, 0.80)
        result["estimated_attempts_for_95_percent"] = _attempts_for_cumulative_target(p, 0.95)

    return result
