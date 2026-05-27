# KRITIS Critical Workflow Inventory

## Inventory objective

Track critical flows that require deterministic state handling, explicit policy guards, and auditable decisions.

## Flow matrix

| Flow | Criticality | Deterministic State Required | Primary Guard |
| --- | --- | --- | --- |
| Mutation approval review/apply | High | Yes | MutationGate + scoped approval artifact |
| Evolver repair apply | High | Yes | approval binding + gate decision |
| High-risk execution trigger | High | Yes | policy evaluator + deny switch |
| Retrieval export to cloud scope | High | Yes | retrieval policy filter + source segregation |
| Routine read-model refresh | Medium | Partial | idempotent refresh rules |
| Non-mutating advisory suggestions | Low | No | informational only |

## Required telemetry for critical flows

- flow identifier + state transition
- decision source (policy/rule id)
- actor or delegated worker identity
- denial reason when blocked
- artifact/task linkage (task_id, proposal_id, target_digest where applicable)

## Immediate gaps to avoid

- implicit success fallback on guard failure
- state transitions without audit event
- mixed-source retrieval output without segregation diagnostics
- mutation apply path without target-bound approval evidence
