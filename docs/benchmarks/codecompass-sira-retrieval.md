# CodeCompass SIRA retrieval benchmark

The deterministic pre-change fixture is
`benchmarks/retrieval/sira_baseline.json`, bound to
`tests/fixtures/retrieval/sira_golden_queries.json`. The fixture covers exact
symbol, bugfix vocabulary gap, architecture vocabulary gap, configuration,
German/English vocabulary, no-result and adversarial input. Its labels are
fixture-verified and do not claim production-repository relevance.

`tests/test_sira_benchmark_fixture.py` rebuilds the legacy
`CodeCompassFtsStore` and proves that stored baseline ranking exactly matches.
The baseline exposes expected vocabulary gaps and records the legacy
adversarial false positive rather than hiding it.

The equally reproducible `benchmarks/retrieval/sira_candidate.json` is rebuilt
through `EnrichedFtsStore`. On this three-document fixture it preserves exact,
config, negative and adversarial behavior and recovers the three intentional
vocabulary gaps. The evaluator reports +0.4286 aggregate Recall@10, nDCG@10,
MRR and evidence coverage. This is a deterministic contract fixture, not a
production-quality or latency claim.

Run candidate comparisons with:

```text
python3 scripts/evaluate_sira_retrieval.py \
  --golden tests/fixtures/retrieval/sira_golden_queries.json \
  --baseline benchmarks/retrieval/sira_baseline.json \
  --candidate <bound-candidate.json> \
  --policy config/retrieval/codecompass-sira-evaluation-policy.v1.json
```

The evaluator rejects binding mismatch and reports Recall@k, nDCG@k, MRR and
evidence coverage per verified query, query class, repository and aggregate,
including paired-delta 95-percent uncertainty intervals. Unverified labels
produce no quality metric. The closed policy additionally requires exact
repository/model/prompt/index bindings, minimum repository/query coverage,
protected-class non-regression and numeric latency, token, cost, index-size and
update-time budgets. Missing or nonnumeric measurements fail the activation
gate; efficiency values are never silently assumed.

The repository fixture intentionally fails the production activation gate: it
has only one tiny corpus, lacks real model/prompt/index binding digests and has
no environment measurements. It remains useful for deterministic contract
regression but cannot activate SIRA.

Production activation requires a separately captured, repository-, model-,
prompt- and index-bound benchmark across Python, TypeScript/JavaScript, Java and
unknown-language repositories. It must compare baseline FTS, existing hybrid,
dense-only, deterministic query normalization, SIRA ablations and agentic
retrieval. No production quality or latency result is asserted by the fixture.
