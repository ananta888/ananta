# CodeCompass SIRA observability

`GET /api/codecompass/sira/status` returns the same redacted status contract used
by the Angular Operations Console. It reports effective mode, model digests and
capabilities, channel flags, base/delta identifiers, artifact count, cache/queue
aggregates when available, and kill-switch state. It does not read Worker files.

Per-query `codecompass.sira-trace.v1` contains routing features/reason, original
query, proposed terms, accept/reject reasons, DF/CF values, compiled weights,
binding digests, reranker status, fallback and lexical-call count. The trace must
not contain credentials, endpoints, denied paths, raw repository documents or
high-cardinality metric labels. UI/API output accepts only an allowlisted Worker
status field set.

Operators can answer:

- Did the Hub choose off, shadow, preferred or required?
- Was the query exact/high-confidence and therefore bypassed?
- Which terms were rejected and why?
- Did index, statistics and repository revisions match?
- Which original, expansion and pointwise contributions affected a candidate?
- Was fallback caused by model, budget, circuit or snapshot state?

Metrics should aggregate expansion calls, cache hits, accepted/rejected terms,
FTS/rerank latency, tokens, cost, index size, incremental update time and
fallback reasons. Query text, path and record IDs belong in access-controlled
traces, not metric labels.
