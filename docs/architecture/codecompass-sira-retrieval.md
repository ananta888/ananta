# CodeCompass corpus-discriminative retrieval architecture

The Hub remains the control plane. It selects mode, model assignments, corpus
scope, budget and fallback policy, validates capabilities, then delegates one
bound request. A Worker executes enrichment, statistics lookup, FTS and optional
reranking. Workers do not dispatch or coordinate other Workers.

The profile is additive:

`Hub profile decision -> Worker router -> query expansion -> scoped DF/CF ->`
`weighted query compiler -> one codecompass_fts call -> existing hybrid fusion`
`-> existing context curation`

There is no second deduplication, fusion or context-packing pipeline. The
`SiraHybridAdapter` emits ordinary `codecompass_fts` candidates and records
original BM25, expansion and pointwise contributions separately. Existing
symbol, exact, graph, vector, policy and budget behavior remains in
`HybridRetrievalService`.

Small protocols separate `StructuredGenerationPort`, `DocumentEnricherPort`,
`QueryExpanderPort`, `CorpusTermStatisticsPort`,
`WeightedLexicalRetrieverPort` and `PointwiseRerankerPort`. This protects SRP,
OCP, ISP and DIP: model/backend adapters can vary without modifying orchestration
or storage, while the Hub/Worker boundary remains explicit and testable.

`CorpusBinding` prevents mixed states by binding tenant, scope, repository
revision, source manifest, index digest, statistics digest, profile version and
active base/delta IDs. Retrieval fails before search when any active value
differs. Generated terms are metadata and never become source truth.

Public clients opt in with the optional `RetrievalRequest.retrieval_profile`.
Old callers and providers remain unchanged. The profile-aware provider is still
registered under `codecompass_fts`; no new channel or client-required field is
introduced.
