# CodeCompass Worker Retrieval Alignment

This follow-up track extends the parent roadmap `todo.worker-sandbox-balanced.json` and does not replace it.

## Parent task mapping

- `WCR-T01..T04` extend governance and rollout tasks (`WSB-T01`, `WSB-T17`, `WSB-T23`, `WSB-T24`).
- `WCR-T05..T08` extend retrieval contracts and indexing lifecycle (`WSB-T19`, `WSB-T20`, `WSB-T27`, `WSB-T29`).
- `WCR-T09..T12` extend hybrid retrieval merge and optional channels (`WSB-T21`, `WSB-T30`, `WSB-T31`, `WSB-T33`).

## Hub mode vs standalone mode

- **Hub mode:** retrieval orchestration remains in hub services; worker consumes bounded context bundles.
- **Standalone mode:** worker may build/load local CodeCompass retrieval stores, but uses the same contracts and bounded execution model.

## Explicit non-overlap

This track does not redefine worker profile semantics, approval ownership, sandbox governance, command execution policy, or standalone contract boundaries from the parent roadmap.

