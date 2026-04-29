# CodeCompass Worker Retrieval Rollout

## Rollout stages

1. **Disabled**  
   All `CODECOMPASS_*` retrieval flags disabled.

2. **FTS only**  
   Enable `CODECOMPASS_FTS_ENABLED=1`, keep vector/graph disabled.

3. **FTS + vector**  
   Enable `CODECOMPASS_VECTOR_ENABLED=1` after FTS baseline stabilizes.

4. **FTS + vector + graph**  
   Enable `CODECOMPASS_GRAPH_ENABLED=1`.

5. **Full hybrid candidate**  
   Enable relation expansion with `CODECOMPASS_RELATION_EXPANSION_ENABLED=1` after benchmark and trace checks.

## Rollback

Rollback is immediate via feature flags:

- disable vector flag for embedding provider incidents
- disable graph/relation flags for missing or stale graph outputs
- keep FTS channel as minimal deterministic fallback

## Expected degraded behavior

- Missing embedding provider: vector channel degrades; FTS/graph stay available.
- Missing graph outputs: graph channel degrades; FTS/vector stay available.
- Stale indexes or manifest mismatch: retrieval cache invalidates and rebuilds bounded stores.

## Validation checklist

- benchmark test produces stable machine-readable mode metrics
- retrieval trace exposes enabled/degraded channels, graph expansion counts and final chunk counts
- worker execution metadata carries retrieval trace linkage fields (`retrieval_trace_id`, `retrieval_context_hash`, `retrieval_manifest_hash`)

