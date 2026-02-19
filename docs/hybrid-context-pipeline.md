# Hybrid Context Pipeline

Hybrid context is executed in explicit stages so behavior is testable and reproducible.

## Stages
1. `scan`: collect repository metadata, docs, and code symbols.
2. `index`: build lexical + semantic indices (LlamaIndex store).
3. `retrieve`: resolve task-relevant context slices with score + source attribution.
4. `route`: pick execution backend (sgpt, aider, opencode, mistral-code) from task intent and policy.
5. `execute`: run planned commands, capture output, gate checks, and trace.

## Contracts
- Every stage emits `trace_id`, `task_id`, `agent`, `ts`, `duration_ms`.
- Retrieval outputs include source file references for explainability.
- Routing outputs include selected backend and fallback list.

## Operational Metrics
- `pipeline_scan_duration_ms`
- `pipeline_retrieve_hit_ratio`
- `pipeline_route_fallback_count`
- `pipeline_execute_success_ratio`

