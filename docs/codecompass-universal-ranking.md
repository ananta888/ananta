# Universal deterministic source ranking

CodeCompass ranks repository sources with `universal-source-ranking.v1` by
default. The ranker is local, deterministic and independent of product,
repository and domain names. Eligibility and source scope are applied before
ranking; they never contribute relevance points.

## Inputs and signals

The complete input is the query, eligible candidates, repository revision,
index digest, scope digest, immutable ranking profile and an optional bound
model digest. The baseline uses bounded lexical path, filename and symbol
overlap, exact symbols, convention-derived file roles, detected entrypoints,
and evidenced graph centrality/proximity when available. Missing graph,
embedding and content signals are reported as partial and contribute zero.

Every result exposes normalized value, weight and contribution for every
declared signal. Scores are tied by confidence, canonical path and canonical
candidate ID. Diversification penalties are explicit contributions. Exact
symbol matches retain rank one.

## Strategy and rollback

The Hub owns strategy selection through
`ANANTA_CODECOMPASS_REPOSITORY_RANKER=universal|shadow|legacy`:

- `universal` is the default and active selection.
- `shadow` serves universal results and records a read-only comparison with
  legacy ranking.
- `legacy` is the explicit rollback path during migration.

Workers do not select strategies or orchestrate other workers.

## Manual overrides

Overrides are disabled by default. A deployment-wide experimental override
must use `ANANTA_CODECOMPASS_RANKING_OVERRIDE_JSON` and contain `owner`,
`reason`, `scope`, `version`, and a future ISO-8601 `expires_at`. Invalid,
expired or incomplete overrides are rejected and their status is included in
the trace. Query-, session- and user-level weight changes are not accepted.

## Evaluation

The deterministic golden scenarios cover Python, TypeScript and Java with
symbol, architecture, implementation and test queries. Evaluation reports
MRR, Recall@K and nDCG@K. Ananta remains a productive smoke case, not an
algorithmic special case.
