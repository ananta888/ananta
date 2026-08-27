# SIRA reference and CodeCompass adaptation

Review date: 2026-08-27. Bound upstream revision:
`facebookresearch/sira@b33f5b71a1870f09b378d8e79afb6ad9e704709b`.
The machine-readable inventory is
[`sira-reference-manifest.json`](sira-reference-manifest.json).

The paper and reference repository describe five practical stages: data
preparation, BM25 indexing, offline corpus enrichment, online query expansion,
and optional pointwise reranking. The core idea is to predict vocabulary that
discriminates within the bound corpus and perform one lexical retrieval action,
rather than creating an unconstrained iterative search loop.

Ananta maps those stages as follows:

| Reference stage | Ananta component | Deliberate difference |
| --- | --- | --- |
| Data preparation | `codecompass_document_normalizer` and SIRA enrichment artifacts | Existing CodeCompass record IDs, scope and manifest provenance remain authoritative. |
| BM25 index | `EnrichedFtsStore` | SQLite FTS5 is used; upstream bm25x is not copied or required. |
| Corpus enrichment | `DocumentEnrichmentService` plus base/delta layers | Generated terms are separate metadata, schema-bounded and prompt-injection filtered. |
| Query enrichment | `QueryExpander` and `CorpusTermValidator` | Exact queries bypass the model; every proposed term must exist in the exact active scope. |
| Pointwise reranking | injected `PointwiseRerankerPort` | Optional, bounded, default-off and never another retrieval loop. |

“SIRA-inspired” means all of the following, and nothing broader: offline
generated search vocabulary is bound to a corpus snapshot; online expansion is
validated with snapshot-specific DF/CF statistics; original terms are always
preserved; accepted terms are compiled into one weighted lexical top-k action;
and optional reranking happens after that action. It is not a claim of result
parity with the paper and does not imply “superintelligent” product behavior.

The reference runtime targets a modern CUDA stack and documents H100 testing.
Its package definition uses tightly pinned PyTorch/Transformers/SGLang versions,
custom indexes and prerelease resolution. Direct adoption would couple Ananta to
those hardware and supply-chain assumptions. The independent implementation
therefore keeps model generation behind existing provider ports and keeps the
deterministic FTS path usable without a GPU or model.

No internal `SRC_*` or `RUN_*` evidence identifiers were supplied. This note
uses the explicit public URLs and commit digest above and does not invent
source-grounding identities.
