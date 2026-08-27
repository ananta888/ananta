# CodeCompass corpus-discriminative lexical retrieval

This optional SIRA-inspired profile helps queries whose vocabulary differs from
repository vocabulary. It enriches documents offline, predicts a few query
terms online, removes terms that do not discriminate inside the exact allowed
corpus, and performs one weighted FTS retrieval. It complements rather than
replaces exact, symbol, graph and vector retrieval.

Quickstart for a local deployment:

1. Keep `CODECOMPASS_SIRA_MODE=off` while building a scope-bound enrichment
   index with `scripts/manage_sira_index.py`.
2. Select a local query model through the central model catalog and set
   `CODECOMPASS_SIRA_QUERY_MODEL`. Private code remains local by default.
3. Set `CODECOMPASS_SIRA_MODE=shadow` and inspect the Operations Console.
4. Run the bound benchmark and security gates. Promote to `preferred` only with
   explicit release approval. The optional reranker remains off.

Exact symbols (`PaymentService.retry`), file paths, hashes and quoted errors skip
model expansion. A German question such as “Wo wird eine Zahlung erneut
versucht?” may add an English corpus term such as `payment retry`, but only when
that term exists in the active allowed index. The original German query remains
in the compiled request. No-result and invalid model output fall back according
to mode without widening scope.

Configuration is defined by `schemas/codecompass.sira-config.v1.json` and is
closed: arbitrary shell commands, providers, endpoints and secrets are rejected.
The profile supports injected local/OpenAI-compatible/CLI model adapters only
through the existing governed model layer; it does not call such backends
directly.

For architecture, lifecycle, fallbacks, observability, benchmark and rollout,
see the corresponding `docs/architecture`, `docs/operations`, `docs/benchmarks`
and `docs/release` documents. “Superintelligent Retrieval Agent” is the research
reference name, not an Ananta performance promise. Hardware, model, data and
cost suitability must be measured locally.
