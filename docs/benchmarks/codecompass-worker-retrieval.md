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

