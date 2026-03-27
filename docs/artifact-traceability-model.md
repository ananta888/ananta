# Artifact Result Views & Traceability Model

This document maps user-facing results to internal execution records.

## User-Facing Principle

Artifacts are the primary output shown to users. Internal traces remain inspectable for authorized operators.

## Lineage Chain

Every artifact should be traceable through:

`goal_id -> plan_id -> plan_node_id -> task_id -> execution_id -> verification_id -> artifact_id`

## Minimal Artifact View (Default)

Default result views should expose:

- artifact title/summary
- creation timestamp
- producing task label
- verification status badge

This keeps first-use experiences simple while preserving internal linkage.

## Drill-Down View (Authorized)

Advanced views can include:

- execution logs and tool actions
- policy evaluation summaries
- verification records with evidence
- related artifacts and prior revisions

## Required Metadata

Each artifact record should include:

- `artifact_id`
- `trace_id`
- `task_id`
- `verification_id` (optional during transition)
- `producer_worker_id`
- `classification`
- `integrity_hash`

## Audit & Governance

Access to advanced trace data must be audited with:

- actor identity
- scope/team
- artifact_id/trace_id
- reason/action
- timestamp

## API Evolution Guidance

- Add optional traceability fields first.
- Maintain legacy artifact response shape.
- Offer compatibility adapters for older clients.
