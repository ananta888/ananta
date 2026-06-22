# Release: CodeCompass Vector Encoding Rollout Plan

**Status:** Draft  
**Date:** 2026-06-22  
**Author:** Ananta Release  
**Feature:** `CODECOMPASS_VECTOR_ENCODING_MODE` and VectorEncodingProfile  
**Scope:** `worker/retrieval/vector_encoding.py`, `worker/retrieval/codecompass_vector_store.py`

---

## Rollout Philosophy

Vector encoding changes how CodeCompass stores and retrieves embeddings. A retrieval regression that goes unnoticed is worse than a feature that stays disabled. Therefore:

1. Start with `off` as the default. Always.
2. Promote modes only after passing quality gates with documented benchmark results.
3. Experimental modes stay experimental until stable results are reproduced across multiple benchmark runs.
4. Rollback steps are documented before any mode is promoted.

---

## Phase 1: Default Off, int8 as Opt-In

**Goal:** Ship the feature as safe infrastructure. No user-visible change by default.

**Configuration:**

```bash
CODECOMPASS_VECTOR_ENCODING_MODE=off      # default; no change to existing behavior
```

**Opt-in for testing:**

```bash
CODECOMPASS_VECTOR_ENCODING_MODE=int8     # safe demo option
CODECOMPASS_VECTOR_ENCODING_SEED=888
CODECOMPASS_VECTOR_ENCODING_STORE_ORIGINAL=false
```

**Release criteria for Phase 1:**

- [ ] `VectorEncodingProfile`, `VectorEncoder`, `EncodedVector` pass all unit tests (green CI).
- [ ] `CodeCompassVectorStore` reads v1 indexes without crash (backward compatibility test).
- [ ] `mode=off` produces identical results to pre-feature behavior.
- [ ] Diagnostics contain no raw vectors, no API keys, no auth headers.
- [ ] `turboquant_mse_experimental` is not the default and not suggested in UI or docs as standard.

---

## Phase 2: float16 / int8 as Recommended Experimental

**Trigger:** Phase 1 deployed and stable; benchmarks run on a real CodeCompass index.

**Promotion criteria:**

- `float16`: Recall@10 ≥ 0.95 across all query types in the standard benchmark set.
- `int8`: Recall@10 ≥ 0.90, Score-Drift (mean) ≤ 0.05, Ranking-Instability ≤ 0.15.
- Benchmarks must be run with fixed seed (`seed=888`), documented manifest hash, and stored output (see `docs/worker/codecompass-vector-quantization-metrics.md`).
- Results must be reproduced in at least 2 independent benchmark runs.

**Labeling after promotion:** `recommended experimental` (not `stable` or `default`).

**UI / CLI display:** Modes labeled `recommended_experimental` show a notice:

```
[experimental] int8 encoding is active. Benchmark results: Recall@10=0.992, Score-Drift=0.011.
Run 'codecompass benchmark' to verify on your index.
```

---

## Phase 3: symmetric4bit and turboquant_mse_experimental — Research / Experimental Only

**These modes remain at status `research/experimental` until:**

- `symmetric4bit`: Recall@10 ≥ 0.85 across all query types AND explicit `experimental` acknowledgement flag set in config.
- `turboquant_mse_experimental`: Recall@10 ≥ 0.85, stable results across 3 separate benchmark runs, documented max_abs_error and compression_ratio.

**Not eligible for promotion to `recommended` without:**

- Documented benchmark results meeting the above thresholds.
- At least one review of the benchmark methodology by a second person.
- Release notes explicitly listing known quality risks and measurement results.

**Labeling:** Always shown as `[experimental]` in UI, CLI, and diagnostics. The `diagnostics.experimental` field must be `true`. Any trace including these modes must surface the experimental flag to the operator.

---

## Rollback Steps

If a deployed encoding mode causes retrieval regression:

### Immediate rollback (environment variable)

```bash
# Set to off and restart service
CODECOMPASS_VECTOR_ENCODING_MODE=off
# OR keep current mode but force float32 fallback:
CODECOMPASS_VECTOR_ENCODING_FAIL_MODE=fallback_float32
```

The store detects `encoding_mode_changed` on next `refresh()` and rebuilds.

### If automatic rebuild is unavailable

```bash
# Delete the encoded index; service auto-rebuilds with mode=off on next start
rm /path/to/codecompass_vector_index.json
```

The v1 index (float32 vectors) will be regenerated on the next `rebuild()` call.

### Verify rollback succeeded

```bash
# Check state block in index file:
python -c "import json; s=json.load(open('codecompass_vector_index.json'))['state']; print(s.get('vector_encoding_profile',{}).get('mode'))"
# Expected output: "off" or "float32"
```

---

## Known Risks and Measurements (to be filled per release)

| Mode | Known Risk | Measurement Required |
|---|---|---|
| `float16` | Slight precision loss on very similar documents | Recall@10 ≥ 0.95 |
| `int8` | Ranking drift for near-zero similarity scores | Score-Drift ≤ 0.05, MRR delta < 0.02 |
| `symmetric4bit` | Significant ranking instability possible | Recall@10 ≥ 0.85, full benchmark required |
| `turboquant_mse_experimental` | Not full TurboQuant_prod; inner-product bias unquantified | 3 benchmark runs, explicit acknowledgement |

This table must be updated with actual measurements before any mode is promoted.

---

## Release Checklist

### Before any mode is labeled `recommended_experimental` or higher:

- [ ] **Benchmark gate:** Benchmark results documented in `docs/worker/codecompass-vector-quantization-metrics.md` format, stored with profile `config_hash`.
- [ ] **Security tests:** Diagnostics checked for raw vector exposure, API key leakage, auth header leakage (test: `tests/test_codecompass_vector_encoding.py` secret injection test green).
- [ ] **Backward compatibility:** v1 index (entries without `encoded_vector`) loads and searches without crash.
- [ ] **Documentation updated:** This rollout doc updated with actual benchmark numbers. `docs/worker/codecompass-vector-index-migration.md` reviewed.
- [ ] **UI/CLI display:** Experimental flag surfaced to operator in diagnostics output.
- [ ] **Rollback verified:** Rollback steps tested in a non-production environment.
- [ ] **Release notes complete:** See template below.

### Before `turboquant_mse_experimental` is labeled anything other than `research/experimental`:

- [ ] Three independent benchmark runs with consistent results.
- [ ] Explicit note in release notes acknowledging it is not a full TurboQuant_prod implementation.
- [ ] `docs/architecture/codecompass-turboquant-scope.md` reviewed and still accurate.

---

## Release Notes Template

```markdown
## CodeCompass Vector Encoding — Release [version]

### New feature: Vector index quantization

CodeCompass can now store embedding vectors in a compact encoded format.
Default: off (no behavior change).

### Available modes (opt-in)

- `off` (default): unchanged behavior, raw float32 vectors.
- `float16`: 2x compression, minimal quality impact.
- `int8`: 4x compression, small ranking drift (benchmark gate required).
- `symmetric4bit` [experimental]: 8x compression, higher ranking risk.
- `turboquant_mse_experimental` [research]: TurboQuant-inspired seam, not production TurboQuant.

### Known risks

[Fill in from benchmark results table above]

### Measurements (this release)

[Fill in Recall@10, Score-Drift, Ranking-Instability per mode]

### Rollback

Set CODECOMPASS_VECTOR_ENCODING_MODE=off and restart.

### Not in scope

This release does NOT include LLM KV-Cache quantization, GPU kernel changes,
or modifications to Ollama/vLLM internals. See docs/architecture/codecompass-turboquant-scope.md.
```

---

## Related

- `worker/retrieval/vector_encoding.py` — implementation
- `worker/retrieval/codecompass_vector_store.py` — store with encoding integration
- `docs/worker/codecompass-vector-quantization-metrics.md` — quality gate definitions
- `docs/worker/codecompass-vector-index-migration.md` — migration guide
- `docs/architecture/codecompass-turboquant-scope.md` — scope boundaries
- `docs/research/turboquant-for-codecompass.md` — research background
