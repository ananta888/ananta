# Hub Fallback And Reliability

This is the canonical specification for hub fallback semantics, reliability guardrails, and operator controls.

It consolidates the previous documents:

- `docs/hub-reliability-guardrails.md`
- `docs/hub-worker-fallback-operator-controls.md`
- `docs/hub_fallback.md`

All guidance is additive and keeps the core model intact: hub as control plane, workers as execution plane, no worker-to-worker orchestration.

## Delegate-First Rule

Normal execution flow:

1. Hub receives goal/task.
2. Hub evaluates policy and worker capability.
3. Hub delegates to an eligible worker.

Hub-local execution is an exception, not the default.

## Fallback Eligibility

Hub fallback is allowed only when all conditions are true:

- no suitable worker is available or reachable,
- policy explicitly allows local fallback for the task class,
- fallback is enabled by operator configuration.

Suggested local execution modes:

- `disabled`
- `fallback_only`
- `always_for_allowed_task_kinds` (strict allowlist)

## Required Provenance

Fallback executions must remain fully traceable. Persist and expose:

- `execution_mode`: `delegated` | `hub_fallback`
- `fallback_reason`: `no_worker` | `policy_denied` | `worker_unreachable` | `worker_failed`
- `delegation_attempted`: boolean
- `worker_candidates`: count
- linkage via `trace_id`, goal/plan/task identifiers

Optional compatibility aliases for existing traces can include:

- `executed_by`
- `delegation_decision`
- `capability_checks`

## Plan Guardrails

Planning must be bounded with explicit, configurable hard limits:

- `max_plan_depth`
- `max_plan_nodes`

Required behavior:

- deterministic abort when one limit is exceeded,
- explicit `limit_exceeded` reason in API/read model,
- no silent bypass through policy overlays.

## Worker Execution Guardrails

Delegated execution should carry a bounded resource envelope:

- max runtime timeout,
- memory ceiling,
- workspace quota.

Workers should report limit exits distinctly from business/task failures. Cleanup must run even on timeout or limit violation.

## Retry Model

Delegation retry should use bounded exponential backoff with jitter:

- retry only transient failures,
- no retries on policy denials or permanent validation failures,
- max attempts configurable by policy.

Log retry count and final disposition under one trace timeline.

## Operator Controls

Recommended controls:

- global fallback enable/disable,
- per-task-kind fallback allowlist,
- max concurrent fallback executions,
- emergency kill switch.

## Observability

Track and surface:

- fallback count/rate,
- fallback reasons,
- duration and outcome of fallback runs,
- policy denials that blocked fallback,
- retry attempts and final status.

## Rollout (Non-Breaking)

1. Introduce defaults via existing config surface.
2. Keep legacy APIs stable.
3. Add metadata fields as optional/read-compatible.
4. Tighten policies gradually per environment.
