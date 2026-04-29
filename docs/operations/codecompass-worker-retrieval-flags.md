# CodeCompass Worker Retrieval Flags

## Flags

- `CODECOMPASS_FTS_ENABLED`
- `CODECOMPASS_VECTOR_ENABLED`
- `CODECOMPASS_GRAPH_ENABLED`
- `CODECOMPASS_RELATION_EXPANSION_ENABLED`

Each flag is independent. Default is disabled (`0`) for safe startup.

## Diagnostics states

- `disabled`: feature flag is off.
- `ready`: enabled and dependency checks passed.
- `degraded`: enabled but partially available (for relation expansion without graph channel).
- `missing_dependency`: enabled but required runtime dependency is unavailable.

## Rollout order

1. FTS only
2. FTS + vector
3. FTS + vector + graph
4. Relation expansion

