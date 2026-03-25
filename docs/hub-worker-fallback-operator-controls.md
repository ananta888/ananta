# Hub-as-Worker Fallback Semantics & Operator Controls

The hub remains the control plane and delegates by default. Fallback execution on the hub is a constrained exception.

## Delegate-First Rule

Normal flow:

1. Hub receives goal/task.
2. Hub selects eligible worker.
3. Worker executes and returns results.

## When Fallback Is Allowed

Fallback to hub execution is permitted only when:

- no eligible worker is available,
- policy allows local execution for the task class, and
- fallback is explicitly enabled in configuration.

## Required Provenance

Fallback executions must include:

- `executed_by = hub`
- `fallback_reason`
- worker candidate evaluation summary
- `trace_id` linkage to goal/plan/task

## Operator Controls

Recommended controls:

- toggle for hub fallback enablement
- per-task-class fallback allowlist
- maximum concurrent fallback executions
- emergency disable switch

## Observability

Expose metrics and logs for:

- fallback count and rate
- fallback reasons by category
- duration and success/failure of hub-run tasks
- policy denials that prevented fallback

## Safety Constraints

- no worker-to-worker delegation paths
- no bypass of policy checks during fallback
- identical audit requirements for worker and hub executions
