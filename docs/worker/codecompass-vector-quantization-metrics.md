# CodeCompass Vector Quantization: Quality Metrics and Gates

**Status:** Reference  
**Date:** 2026-06-22  
**Author:** Ananta Architecture  
**Scope:** Retrieval quality measurement for `CodeCompassVectorStore` quantization modes

---

## Principle

Every quantization mode must be measured against an unquantized **float32 baseline index** before it can be promoted from `experimental` to `opt-in` or `recommended`. Compression without quality accountability is unacceptable.

---

## Baseline

The float32 baseline is always:
- `VectorEncodingProfile(mode="float32", store_original=False)` — or `mode="off"` (equivalent for measurement purposes).
- Same documents, same embedding provider, same embedding text profile.
- Same random seed (`seed=888` by default) for reproducibility.
- Fixed at benchmark time and not changed between candidate runs.

All metrics are reported as **delta from float32 baseline**, not as absolute values.

---

## Metrics

### Recall@K

Measures whether the correct result appears in the top-K results of the quantized index when compared to the float32 index.

```
Recall@K = (queries where float32_top1 appears in quantized_topK) / total_queries
```

Tracked at K = 1, 5, 10.

- **Recall@1**: exact top-hit preservation. Important for symbol-name queries.
- **Recall@5**: practical retrieval quality for ranked context windows.
- **Recall@10**: lenient quality gate; used as minimum threshold for promotion.

### MRR (Mean Reciprocal Rank)

```
MRR = mean(1 / rank_of_float32_top1_in_quantized_results)
```

Captures how far the correct result slips in the ranking. A result at rank 3 instead of rank 1 is penalized but not catastrophic.

### NDCG (Normalized Discounted Cumulative Gain)

Standard information retrieval metric. Uses float32 ranking as ideal ordering. Measures ranking quality, not just hit/miss. Computed at K=10.

### Score-Drift

```
Score-Drift = mean(|cosine_score_float32(q, d) - cosine_score_quantized(q, d)|)
              across all (query, document) pairs in the benchmark set
```

Measures the absolute change in similarity scores introduced by quantization. High score-drift indicates that downstream ranking decisions based on score thresholds may behave differently.

### Ranking-Instability

```
Ranking-Instability = fraction of queries where the top-3 result set differs
                      between float32 and quantized index
```

A ranking-unstable mode should not be promoted even if Recall@10 is acceptable, because it produces non-reproducible context selection.

---

## Query Types

Benchmarks must cover all query types that CodeCompass users produce in practice:

| Query Type | Example | Notes |
|---|---|---|
| **Symbol name** | `VectorEncoder`, `_deterministic_sign_rotation` | Exact match often; tests Recall@1 |
| **Technical description** | `"how does the 4-bit encoding pack nibbles"` | Tests semantic retrieval |
| **Mixed DE/EN** | `"Wie funktioniert der Fallback bei Encoding-Fehler"` | Common in Ananta codebase |
| **File/path-near** | `"worker/retrieval quantization store"` | Tests path-aware ranking |
| **Refactoring intent** | `"replace direct cosine similarity with encoded vector decode"` | Tests cross-file retrieval |

Each query type should have at least 20 queries in the benchmark set. Total recommended: 100+ queries.

---

## Configurable Quality Gates

Gates are defined in configuration and evaluated as part of `VectorEncodingBenchmark.run()`:

```python
@dataclass
class VectorEncodingQualityGate:
    min_recall_at_10: float = 0.90       # default: 90% of float32 top results recovered
    max_score_drift: float = 0.05        # default: max 0.05 absolute cosine score drift
    max_ranking_instability: float = 0.15  # default: max 15% of queries change top-3
    fail_mode: str = "block"             # block | fallback_float32 | warn_only
```

### Fail Modes

| Mode | Behavior |
|---|---|
| `block` | Index rebuild fails; quantized index is not written; existing index retained |
| `fallback_float32` | Quantized index is built but marked as `quality_gate_failed`; store automatically uses float32 for all queries |
| `warn_only` | Index is built and used; gate failure is logged and surfaced in diagnostics; operator must acknowledge |

Default: `block` for experimental modes, `warn_only` for `float16` and `int8` on first deployment.

---

## Reproducibility

All benchmark runs must be reproducible:

- Fixed random seed for query sampling: `seed=888` (matches default `VectorEncodingProfile.seed`).
- Fixed document set: snapshot of the CodeCompass index at a known manifest hash.
- Fixed embedding provider: same provider config hash as the index being tested.
- All metric values stored in benchmark output with the profile `config_hash` and manifest hash.
- Re-running with the same inputs produces identical metric values.

---

## Output Format

```json
{
  "benchmark_id": "codecompass-vector-encoding-benchmark",
  "encoding_mode": "int8",
  "profile_config_hash": "a3b4c5d6e7f8...",
  "manifest_hash": "...",
  "seed": 888,
  "baseline_mode": "float32",
  "query_count": 120,
  "metrics": {
    "recall_at_1": 0.967,
    "recall_at_5": 0.983,
    "recall_at_10": 0.992,
    "mrr": 0.971,
    "ndcg_at_10": 0.988,
    "score_drift_mean": 0.011,
    "score_drift_max": 0.034,
    "ranking_instability": 0.025
  },
  "quality_gate": {
    "min_recall_at_10": 0.90,
    "max_score_drift": 0.05,
    "max_ranking_instability": 0.15,
    "passed": true,
    "fail_mode": "block"
  },
  "by_query_type": {
    "symbol_name": { "recall_at_1": 0.985, "recall_at_10": 1.000 },
    "technical_description": { "recall_at_1": 0.950, "recall_at_10": 0.983 },
    "mixed_de_en": { "recall_at_1": 0.940, "recall_at_10": 0.980 },
    "file_path_near": { "recall_at_1": 0.970, "recall_at_10": 0.995 },
    "refactoring_intent": { "recall_at_1": 0.925, "recall_at_10": 0.975 }
  }
}
```

---

## Fallback Strategies

When quality gates fail, the store selects a fallback strategy based on `fail_mode`:

### `block`

```
IndexRebuildError: vector_encoding_quality_gate_failed
  reason: recall_at_10=0.87 < min_recall_at_10=0.90
  action: quantized index not written; existing index retained
```

### `fallback_float32`

The quantized index is built on disk but tagged:

```json
{
  "state": {
    "vector_encoding_profile": { "mode": "int8" },
    "quality_gate_passed": false,
    "quality_gate_fail_reason": "recall_at_10=0.87",
    "active_search_mode": "float32_fallback"
  }
}
```

Queries run against the float32 vectors in the index (either stored via `store_original=true` or rebuilt inline).

### `warn_only`

Index is built and used as-is. Diagnostics include:

```json
{
  "vector_encoding_quality_gate": "failed",
  "quality_gate_fail_reason": "score_drift_mean=0.07 > max_score_drift=0.05",
  "experimental": true
}
```

Operator must acknowledge in the next benchmark run or configuration change.

---

## Promotion Criteria

| Mode | Minimum Gate to Promote |
|---|---|
| `float16` | Recall@10 ≥ 0.95 across all query types |
| `int8` | Recall@10 ≥ 0.90, Score-Drift ≤ 0.05 |
| `symmetric4bit` | Recall@10 ≥ 0.85, explicit `experimental` acknowledgement |
| `turboquant_mse_experimental` | Recall@10 ≥ 0.85, stable across 3 benchmark runs |

No mode is promoted from `experimental` to `recommended` without documented benchmark results meeting these gates.

---

## Related

- `worker/retrieval/vector_encoding.py` — VectorEncodingProfile and VectorEncoder
- `worker/retrieval/codecompass_vector_store.py` — store with quality gate integration point
- `docs/release/codecompass-vector-encoding-rollout.md` — rollout gates referencing benchmark results
- `docs/architecture/codecompass-turboquant-scope.md` — scope boundaries
