# CodeCompass Worker Retrieval Benchmark

This benchmark compares deterministic retrieval modes on the fixture:

`tests/benchmarks/codecompass_worker/fixtures/java_spring_xml/`

## Modes

1. `baseline_lexical` (knowledge-index-like lexical fallback)
2. `fts_only`
3. `vector_only`
4. `fts_vector`
5. `graph_expanded`
6. `full_hybrid`

## Metrics

- `Recall@k`
- `MRR`
- `selected_token_count`
- `latency_ms`
- `explanation_coverage`

All metrics are emitted in machine-readable form by:

`tests/benchmarks/codecompass_worker/test_codecompass_hybrid_retrieval_benchmark.py`

No external LLM or external embedding service is required. Vector mode uses deterministic fake embeddings.
## Embedding Provider Benchmark Profile

Use this profile to compare pipeline behavior with deterministic hash embeddings
against an optional real local/OpenAI-compatible embedding endpoint.

The benchmark must run offline by default:

```json
{
  "name": "codecompass-worker-retrieval-localhash",
  "embedding_provider": {
    "provider": "local_hash",
    "model_version": "hash-v1",
    "dimensions": 12,
    "external_calls_allowed": false
  },
  "metrics": ["recall_at_k", "mrr", "top_k_hit_rate"],
  "required": true
}
```

Optional real-provider runs are allowed only when explicit local config is set:

```json
{
  "name": "codecompass-worker-retrieval-real-optional",
  "embedding_provider": {
    "provider": "openai_compatible",
    "base_url": "http://localhost:11434/v1",
    "allowed_base_urls": ["http://localhost:11434"],
    "external_calls_allowed": true
  },
  "required": false
}
```

Interpretation:

- `local_hash` validates indexing, scoring plumbing and deterministic CI
  behavior, not semantic quality.
- real-provider runs may compare semantic quality but must never be required for
  CI.
- report metrics separately per provider; do not average hash and real-provider
  results into one score.

