# Product Events

Ananta separates product events from low-level technical logs. Product events describe user-visible milestones in core flows and are stored through the audit stream with a `product_` action prefix and a `details.product_event` hub-event envelope.

## Event Channel

- Channel: `product`
- Envelope: standard hub event (`version`, `kind`, `channel`, `event_type`, `timestamp`, `actor`, `context`, `details`)
- Storage: `AuditLogDB` via `log_audit`
- Audit action prefix: `product_`

## Core Events

| Event | Meaning | Typical source |
| --- | --- | --- |
| `product_flow_started` | A user-visible flow started. | Goal planning request |
| `goal_created` | A goal record exists and can be referenced. | `POST /goals` |
| `goal_planning_succeeded` | Planning produced a visible result. | Auto-planner completion |
| `goal_planning_failed` | Planning ended without a usable result. | Auto-planner error |
| `goal_blocked` | The goal could not proceed due to missing input, readiness or policy. | Validation/precondition |
| `review_required` | A manual review decision is required before continuing. | Governance/review services |

## Required Dimensions

Every product event should include, when available:

- `goal_id`
- `trace_id`
- `plan_id`
- `source` (`ui`, `cli`, `api`, `demo`, `trigger`)
- `mode` or shortcut
- `created_task_count`
- `reason` for blocked, failed or review-required states

## Core Flow Coverage

Start:
- `product_flow_started`
- `goal_created`

Goal creation:
- `goal_created`
- `goal_planning_succeeded`

Blocking:
- `goal_blocked`

Review:
- `review_required`

Success:
- `goal_planning_succeeded`

Abort/failure:
- `goal_planning_failed`

## Interpretation

Product metrics should count these events separately from technical logs. Technical logs explain how the system behaved; product events explain what users experienced in the official flows.

## Operational Aggregates

The dashboard read model exposes product-event aggregates under:

`llm_configuration.runtime_telemetry.operations.product_events`

The aggregate separates:

- friction: blocked goals, review-required states, planning failures and success counts/rates
- channels: UI, CLI, API, demo or trigger source counts and source-specific friction
- usage contexts: demo, trial and production counts with context-specific friction

This keeps product improvement signals separate from low-level technical logs while still using the audit stream as the durable source.
