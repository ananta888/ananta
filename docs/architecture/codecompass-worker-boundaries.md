# CodeCompass Worker Boundaries

## Hub ownership in hub mode

- Retrieval governance and policy selection.
- Channel enablement defaults and rollout switches.
- Context bundle assembly and provenance persistence.
- Retrieval observability and benchmark interpretation.

## Standalone worker ownership

- Local loading of CodeCompass output manifests and records.
- Local FTS/vector/graph stores within workspace boundaries.
- Bounded retrieval expansion under worker profile limits.

## Forbidden ownership shift

Execution backends (`ananta-worker`, `opencode`, `codex`) must not own global retrieval policy or cross-repository indexing governance.

## Integration seams

- `ContextBundleService` output contract.
- `RetrievalService` retrieval payload contract.
- Worker context manifest and retrieval provenance metadata.

