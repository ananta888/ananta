# Incremental Adoption Strategy: Tasks to Goals

This guide outlines a safe migration path from task-first clients to goal-first workflows.

## Migration Goals

- Preserve existing task APIs.
- Introduce goal and plan APIs additively.
- Allow gradual team-by-team rollout.

## Feature-Flag Sequence

1. `goals_api_enabled`
2. `persisted_plans_enabled`
3. `artifact_trace_enrichment_enabled`
4. `strict_policy_enforcement_enabled`

Each flag should be independently reversible.

## Coexistence Model

- Legacy clients submit tasks directly.
- New clients submit goals, hub generates plans and tasks.
- Both paths use shared execution and verification infrastructure.

## Compatibility Adapters

Provide adapters that:

- map legacy task payloads to internal goal/task structures where needed
- preserve legacy response fields
- include new metadata only as optional fields

## Rollout Checklist

- Enable flags in staging.
- Validate goal and legacy task paths in parallel.
- Monitor audit, trace and verification consistency.
- Expand rollout by team after stability checks.

## Decommission Criteria

Legacy-only paths can be deprecated only after:

- documented migration window
- telemetry confirms low legacy usage
- equivalent goal-first behavior validated
