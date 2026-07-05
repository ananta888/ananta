# CodeCompass

Ananta CodeCompass is the combined context layer that feeds the
worker tool loop. It is **not** a copy of any external tool — it
integrates over a small set of contracts and lets the Hub/Worker
plumbing stay versioned and reproducible.

## Composition

| Layer | Source | Contract |
|-------|--------|----------|
| Symbolgraph (functions, classes, imports, calls, inheritance, tests) | optional CRG v2.3.6 (commit `b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d`) | `codecompass.graph_artifact.v1` |
| Repository Intelligence (build targets, runners, tests, packages, coverage) | CMake File API via SPADE ref-impl `6306e203732f7c4553d1564c5250396b7f84a315` (pinned) + manual fixtures | `codecompass.repository-intelligence.v1` |
| Semantic / RAG (snippets, docs, embeddings) | existing CodeCompass retrieval | `codecompass.semantic_translation_graph.v1` |
| Policy / Trust / Evidence | in-house | `codecompass.graph-evidence.v1` |

## Position relative to CRG and RIG/SPADE

| Aspect | Ananta CodeCompass | CRG v2.3.6 | SPADE ref-impl |
|--------|--------------------|-----------|---------------|
| Native AST graph | yes (RIG-001 + CRG-001 docs) | yes | no |
| Build/Target/Runner truth | yes (RIG-001 + RIG-003) | no | yes |
| Build-system coverage | CMake (M2/M3); npm/maven/gradle/cargo/go via RIG-012 manual fixtures (M7); full automation in M8+ | n/a | CMake only |
| Evidence model | trust_level + verification_status + source_id provenance (COMBO-002) | heuristic confidence_kind | reply ids |
| Failure model | fail-closed at import edge (DD-013/CCRIG-DD-007) | exception | exception |

## What Ananta does NOT do

* does **not** vendor CRG or SPADE
* does **not** call upstream floating-main revisions
* does **not** use a free-form graph query language (CCRIG-DD-004)
* does **not** treat missing evidence at partial/unknown coverage as
  negative evidence (CCRIG-DD-008)
* does **not** synthesise source / run IDs (AGENTS.md)

## Architecture Documents

* `architecture/code-review-graph-adapter.md` — CRG-001
* `architecture/repository-intelligence-graph.md` — RIG-001
* `architecture/codecompass-import-trust.md` — DD-017 + DD-013 + DD-016
* `codecompass-tools.md` — public tool registry
* `ci/ananta-codecompass-review.md` — CRG-011 CI workflow

## Backlog

See `todos/todo.codecompass-crg-rig-spade-integration.json`.

## Status

The M1–M5 milestones are complete; M6/M7 close out the integration
with the worker tool loop, e2e fixtures, metrics, and a final security
audit. See the backlog for individual task status.