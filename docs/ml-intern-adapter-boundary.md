# Optional ml-intern Adapter Boundary

## Scope

This boundary defines how ml-intern-style workers can be delegated as specialized execution backends while hub ownership remains intact.

## Hub-owned responsibilities (not delegated)

- task queue ownership
- planning and decomposition
- routing and fallback policy
- approval and governance decisions
- final verification and status transitions

## Adapter-owned responsibilities (delegated execution only)

- execute a bounded task payload
- return structured result, artifacts and diagnostics
- report capability profile and health signals

## Required adapter contract

1. **Input contract**
   - `task_id`, `trace_id`
   - execution intent (`task_kind`, requested capability class)
   - bounded context bundle reference
   - explicit tool/operation limits

2. **Output contract**
   - status (`completed` | `failed` | `blocked`)
   - normalized result payload
   - artifact references with provenance
   - diagnostics (`latency`, failure category, retry metadata)

3. **Policy contract**
   - adapter cannot fan out orchestration
   - adapter cannot override hub approval/routing outcomes
   - adapter actions stay auditable through hub events

## Leakage prevention

- no adapter-owned re-planning of other workers
- no hidden long-running autonomous loops
- no direct mutation of hub governance state
