# Hierarchical Architecture Context

Stand: 2026-08-17 · Track: `codecompass-hierarchical-architecture-context`

CodeCompass can prefill a small architecture map and let agents zoom
with tools. The full graph never enters the prompt.

## Levels

| Level | Purpose | Typical relations | Minimum evidence |
|---|---|---|---|
| system | product/platform frame | contains, governed_by | snapshot + name |
| subsystem | domain or bounded context | contains, uses, depends_on | path or domain record |
| component | service/package/worker | uses, exposes_tool, stores | file or package record |
| file | concrete source | contains, implements | path |
| symbol | function/class | calls, implements | path + symbol |

`contains`, `uses`, `depends_on`, `implements`, `exposes_tool`, `stores`,
`retrieves_from`, `governed_by` and `provides_context_to` are
deterministic when they come from typed graph edges. `calls` from
`calls_probable_target` is inferred.

## Example

Ananta (system) → CodeCompass (subsystem) → Context Planner (component)
→ `agent/services/codecompass_context_planner_service.py` (file) →
`plan_architecture_prefill` (symbol).

## Budgets

`ArchitectureBudget` is nested under the existing CodeCompass token
ceiling. Truncation is explicit (`truncated`, `truncation_reason`,
`expansion_handles`).

## Retrieval

Prefill and expand use the same
[agentic retrieval](agent-codecompass-hybrid-retrieval.md) capability
rules. Hierarchical projection is a view, not a second store.
