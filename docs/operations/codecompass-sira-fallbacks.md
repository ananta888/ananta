# CodeCompass SIRA failures and fallbacks

Modes are `off`, `shadow`, `on_demand`, `preferred` and `required`.

- `off`: original CodeCompass FTS/Hybrid only.
- `shadow`: computes both results, returns only baseline results and marks the
  shadow result non-effecting.
- `on_demand`: runs only when deterministic routing inputs request it.
- `preferred`: uses SIRA when ready and falls back to original FTS.
- `required`: returns a typed error instead of silently changing semantics.

Stable reasons include `corpus_unavailable`, `circuit_open`,
`model_budget_unavailable`, `sira_manifest_mismatch`, `sira_index_mismatch`,
`sira_statistics_mismatch`, `expansion_model_error`,
`expansion_schema_invalid`, `sira_weighted_query_empty`,
`reranker_timeout` and `reranker_error`. Non-required relevance failures may
fall back. Tenant, scope, revision, allowed paths, data policy and SourceRef
validation never fail open.

Three consecutive SIRA execution failures open the in-process Worker circuit by
default. While open, non-required requests use baseline and required requests
fail. After the recovery interval, one request is admitted; success closes the
circuit. There is no retry or Worker-to-Worker loop.

The production SIRA hot path records exactly one lexical retrieval call. Shadow
mode intentionally records two calls to compare baseline and candidate; only the
baseline affects output. Reranking is not a retrieval call and preserves the
ungoverned candidate set on timeout or model error.
