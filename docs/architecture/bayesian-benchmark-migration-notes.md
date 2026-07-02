# Bayesian Benchmark — Migration Notes

## Files Changed

### New Files

| File | Purpose |
|---|---|
| `agent/services/bayesian_benchmark_estimator.py` | Pure Bayesian estimator — no I/O, no side effects |
| `agent/services/bayesian_benchmark_adapters.py` | Adapter layer — converts existing sample dicts to evidence records |
| `tests/test_bayesian_benchmark_estimator.py` | Unit tests: 55 tests covering estimator math and edge cases |
| `tests/test_bayesian_benchmark_adapters.py` | Adapter tests: 26 tests covering all three benchmark sources |
| `tests/test_bayesian_benchmark_compat.py` | Backward-compatibility tests: 26 tests, no Bayesian fields break existing consumers |
| `docs/architecture/bayesian-benchmark-interpretation.md` | Concept guide, worked example, API reference, routing policy boundaries |

### Modified Files

| File | Change |
|---|---|
| `agent/hub_benchmark.py` | Added `include_bayesian: bool = False` to `recommend_hub_model`, `recommend_hub_models`, `hub_benchmark_rows` |
| `agent/ollama_benchmark.py` | Added `include_bayesian: bool = False` to `recommend_ollama_model`, `recommend_ollama_models`, `ollama_benchmark_rows` |
| `agent/llm_benchmarks.py` | Added `include_bayesian: bool = False` to `recommend_model_for_context`, `recommend_models_for_context`, `benchmark_rows` |

---

## Compatibility

### Existing benchmark result JSON files remain valid

No changes to `hub_benchmark_results.json`, `ollama_benchmark_results.json`, or `llm_model_benchmarks.json`
schemas. The estimator reads existing `samples` arrays in-place without writing back.

### Existing callers are unaffected

All modified functions default to `include_bayesian=False`. Any caller that does not pass
`include_bayesian=True` receives exactly the same dict shape as before. Bayesian keys never
appear in the default return value.

### No database migration required

Bayesian estimates are computed on-demand from existing `samples` arrays. No new persistence
format, no schema version bump, no ALTER TABLE, no migration script.

### No new live benchmark run required

Estimates are derived from existing samples that are already present in the benchmark result
files. A model with 20+ existing samples immediately gets a meaningful Bayesian estimate
without running any new benchmarks.

---

## Rollback

To roll back the Bayesian display without touching existing benchmark data:

1. Revert changes to `agent/hub_benchmark.py`, `agent/ollama_benchmark.py`,
   `agent/llm_benchmarks.py` (remove `include_bayesian` parameter and blocks).
2. Remove `agent/services/bayesian_benchmark_estimator.py` and
   `agent/services/bayesian_benchmark_adapters.py`.
3. Remove the three new test files.

**The existing `hub_benchmark_results.json` and `ollama_benchmark_results.json` sample data
is NOT touched by rollback.** Benchmark history is preserved independently of whether the
Bayesian layer is present.

---

## Test Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `tests/test_bayesian_benchmark_estimator.py` | 55 | Posterior math, zero evidence, uncertainty labels, cumulative estimates, edge cases |
| `tests/test_bayesian_benchmark_adapters.py` | 26 | Hub/Ollama/llm_benchmark evidence extraction, filter logic, enrich helpers |
| `tests/test_bayesian_benchmark_compat.py` | 26 | Default payloads unchanged, Bayesian keys added only on request, malformed samples handled |
| **Total** | **107** | All pass |

Frontend tests (Angular benchmark dashboard): pending BAYES-016.

---

## What Was Explicitly Not Changed

- No new benchmark runner was introduced.
- No duplicate benchmark result store was created.
- No new model router was added.
- `suitability_score` values and computation are unchanged.
- Hub routing policy is unchanged; Bayesian estimates are advisory only.
- Autopilot hard guards, planning-track validation, and hub-worker boundaries are untouched.
