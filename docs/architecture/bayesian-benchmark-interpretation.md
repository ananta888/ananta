# Bayesian Benchmark Interpretation

## Purpose

Ananta's benchmark system (hub, Ollama, llm_benchmarks) already records
per-call success/failure, quality-gate results, latency, token counts, and
cost. This layer adds **advisory** posterior probability estimates on top of
those existing samples without replacing any existing score, routing policy,
or benchmark runner.

The key question the Bayesian layer answers:

> Given the evidence we have so far, what is the probability that this model
> produces a quality result on the next attempt — and how many attempts would
> it take to be reasonably confident of getting at least one?

---

## Core Concepts

### Prior

Before any benchmark evidence exists, the estimator starts from a
**Beta(1, 1)** prior — the uniform distribution over [0, 1]. This means:

- Zero evidence → `posterior_mean = 0.5` (genuine uncertainty, not 50%
  confidence).
- The prior is intentionally non-committal: it never produces a certainty
  claim from no evidence.
- The prior strength is configurable; the default does not change routing.

### Evidence

Each benchmark sample becomes one Bayesian evidence record with:

| Field | Meaning |
|---|---|
| `success` | Raw LLM call returned a non-empty response |
| `quality_passed` | Quality-gate evaluation passed (deterministic signal) |
| `deterministic_signal` | `quality_passed` if available, else `success` |

Evidence is **not** fabricated. Absence of quality data is treated as missing
evidence (not a failure).

### Posterior

After observing *k* successes in *n* trials with a Beta(α, β) prior:

```
posterior = Beta(α + k, β + (n − k))
posterior_mean = (α + k) / (α + β + n)
```

The posterior is always strictly between 0 and 1 for any finite evidence.
A 90% credible interval is reported alongside the point estimate.

**Quality-gate evidence is preferred over raw success.** When
`quality_passed` values are present, they form the primary signal because
a passing quality gate is stronger, deterministic evidence than a non-empty
LLM response.

---

## Worked Example

A small model (e.g. Qwen 2.5 3B) has produced 3 successes and 2 failures
in quality-gate evaluations (`quality_passed`):

```
Prior:            Beta(1, 1)       → mean = 0.50
After 3S + 2F:    Beta(4, 3)       → posterior_mean ≈ 0.571
90% CI:           [0.22, 0.91]     (high uncertainty, only 5 samples)
```

Cumulative success after N independent attempts:

| N | P(≥ 1 success) |
|---|---|
| 1 | 57% |
| 2 | 82% |
| 3 | 93% |
| 5 | 98% |

**Interpretation**: Despite modest per-attempt probability, 3–4 retries of
this cheap model reaches high cumulative confidence. The Bayesian layer
surfaces this estimate — the routing decision whether to actually retry
remains with the hub policy.

---

## Attempt Estimate Math

Given posterior mean *p*:

```
P(≥ 1 success in N attempts) = 1 − (1 − p)^N

Minimum N for target T:
  N = ⌈log(1 − T) / log(1 − p)⌉
```

Three modes are supported:

| Mode | Description |
|---|---|
| `independent` | Standard: each attempt is independent |
| `pessimistic` | Effective *p* decreases with N (models correlated failures) |
| `optimistic` | Effective *p* increased by `optimism_factor` (models retry strategies) |

---

## Assumptions and Caveats

1. **Correlated attempts**: The independent mode assumes each retry has the
   same probability of success as the first. In practice, LLM retries on the
   same context may be correlated (repeated errors). Use pessimistic mode
   when correlation is suspected.

2. **Benchmark overfitting**: Posterior estimates are only as good as the
   benchmark prompts. A model may pass planning benchmarks but fail on
   production tasks if prompts are not representative.

3. **Deterministic quality gates are stronger evidence** than LLM
   self-assessment. Tests, compilation results, and structured output
   validators are more reliable signals than raw `success` flags.

4. **Small sample instability**: With fewer than 5 samples, the 90% credible
   interval spans most of [0, 1]. The `low_confidence` flag is set in this
   case.

5. **This layer does not prove code correctness.** It improves benchmark
   interpretation and planning estimates. Correctness is established by
   deterministic tests.

---

## Uncertainty Labels

| Label | Condition | `low_confidence` |
|---|---|---|
| `no_evidence` | 0 samples | `true` |
| `very_low` | < 5 samples | `true` |
| `low` | 5–19 samples, high variance | `true` |
| `medium` | 5–19 samples, low variance OR ≥ 20 samples, high variance | varies |
| `high` | ≥ 20 samples, low variance | `false` |

High confidence requires both **sufficient samples** and **stable evidence**
(low posterior variance).

---

## Routing Policy Boundaries

Bayesian estimates in this implementation are **advisory only**:

- They do **not** change which model is selected automatically.
- They do **not** override existing `suitability_score` rankings.
- They do **not** escalate tool permissions or bypass hub policy.
- They do **not** grant capability access based on benchmark statistics.

The estimates appear in recommendation and row payloads under the optional
`bayesian_estimate` key when callers pass `include_bayesian=True`. Existing
consumers that do not request this key are unaffected.

### How a Future Routing Policy Could Use These Estimates

A future routing policy could use these fields to make evidence-based
escalation decisions:

```
if candidate.estimated_attempts_for_80_percent <= retry_budget:
    # Cheap model worth retrying before escalation
    use_small_model_with_retries()
else:
    # Expected cost of retries exceeds budget → escalate
    use_larger_model()
```

Any such policy change must be implemented as an explicit separate task with
its own contract, tests, and hub policy review — not by changing benchmark
statistics thresholds alone.

### Fallback / Escalation Criteria (Advisory)

The Bayesian layer surfaces when retry-before-escalation is statistically
sensible:

- `estimated_attempts_for_80_percent` ≤ configured retry budget: retry
  is cost-effective.
- `estimated_attempts_for_80_percent` > budget AND `low_confidence=true`:
  insufficient evidence to recommend either path; human review or a larger
  model is safer.
- `posterior_quality_probability` < 0.2: model has consistently failed
  quality gates; escalation is likely warranted regardless of retry budget.

These criteria reference existing benchmark recommendation paths. No new
model router is proposed or implemented.

---

## API

All existing `recommend_*` and `*_rows` functions accept an optional
`include_bayesian: bool = False` keyword argument. When `True`, each
returned dict gains a `bayesian_estimate` key:

```python
from agent.hub_benchmark import recommend_hub_models

results = recommend_hub_models(
    data_dir=data_dir,
    role_name="planner",
    include_bayesian=True,
)
# results[0]["bayesian_estimate"]["posterior_quality_probability"]  → float
# results[0]["estimated_attempts_for_80_percent"]                  → int | None
# results[0]["low_confidence"]                                      → bool
```

Adapters for direct evidence extraction:

```python
from agent.services.bayesian_benchmark_adapters import extract_hub_evidence
from agent.services.bayesian_benchmark_estimator import compute_posterior

evidence = extract_hub_evidence(bucket, provider="lmstudio", model="llama3", role_name_filter="planner")
posterior = compute_posterior(evidence=evidence, signal_key="quality_passed")
```

See `agent/services/bayesian_benchmark_estimator.py` and
`agent/services/bayesian_benchmark_adapters.py` for full API reference.
