# Worker Capability Routing & Policy Explainability

This document describes how the hub selects workers while preserving the hub–worker architecture constraints.

## Scope

- **Hub role:** planning, routing, policy and governance decisions.
- **Worker role:** execution of delegated tasks only.
- **No worker-to-worker orchestration:** all delegation decisions originate in the hub.

## Routing Inputs

The hub computes routing decisions from additive metadata:

1. **Task requirements:** capabilities, tool classes, expected runtime, isolation class.
2. **Worker capabilities:** declared skills, supported tools, trust level, team scope.
3. **Policy constraints:** tenant boundaries, security level, approval requirements.
4. **Runtime health:** worker liveness, queue depth, recent failure rate.

## Routing Decision Pipeline

1. Build candidate worker list from capability match.
2. Filter candidates by policy constraints (team, role, sensitivity).
3. Apply governance checks (allowed tool classes, execution guardrails).
4. Rank candidates by reliability and latency hints.
5. Delegate task to top-ranked eligible worker.
6. Fall back to hub execution only when allowed and no eligible worker exists.

## Explainability Fields

Each routing decision should be stored with:

- `trace_id`
- `goal_id`, `plan_id`, `task_id`
- `selected_worker_id` (or `hub`)
- `candidate_count`
- `policy_checks[]` with `rule_id`, `result`, `reason`
- `worker_profile`, `profile_source`
- `worker_runtime_path` (`native_worker_pipeline` vs `sgpt_fallback_proxy`)
- `policy_classification_summary`
- `fallback_reason` when hub executes
- `timestamp`

## Operator Visibility

Operators should be able to answer:

- Why this worker was selected.
- Why other workers were rejected.
- Which policy blocked a candidate.
- Whether fallback to hub occurred and why.

## Compatibility & Rollout

- Keep existing task APIs stable.
- Add optional explainability fields in responses/events.
- Gate strict policy checks with feature flags during migration.

## Security Notes

- Default deny for sensitive capabilities.
- Least-privilege worker scopes by team.
- Immutable audit entries for routing decisions.
