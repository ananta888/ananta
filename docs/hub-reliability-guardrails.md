# Hub Reliability & Guardrails (Task Bundle)

This document summarizes an incremental, architecture-safe implementation bundle that completes five related backlog tasks:

- ARCH-HUB-817
- ARCH-PLAN-920
- ARCH-EXEC-921
- ARCH-HUB-922
- REL-RETRY-931

All changes are additive and preserve the core model: **the hub remains the control plane, workers execute delegated work, and fallback is explicit and observable**.

## 1) Delegate-first hub with explicit fallback (ARCH-HUB-817)

The hub must always prefer delegation when a suitable worker is available. Hub-local execution is allowed only as a controlled fallback path.

Decision points:

1. Capability and policy check for candidate workers.
2. Delegation attempt to matching worker.
3. Fallback eligibility check (policy + operator configuration).
4. Local execution only when delegation is not possible or explicitly denied.

Safety constraints:

- No worker-to-worker orchestration paths.
- Fallback decisions must be auditable.
- Policy denial and technical unavailability are distinct reasons.

## 2) Plan generation limits (ARCH-PLAN-920)

To prevent runaway loops in planning, enforce explicit limits:

- `max_plan_depth` (safe default, configurable)
- `max_plan_nodes` (safe default, configurable)

Behavior:

- Hub aborts generation when one limit is exceeded.
- Response includes a deterministic limit-exceeded reason.
- Policy can tighten limits but should not bypass them silently.

## 3) Worker execution resource guardrails (ARCH-EXEC-921)

Each delegated execution should be constrained by explicit resource envelopes:

- Maximum runtime (timeout)
- Memory ceiling
- Workspace quota

Requirements:

- Constraints are assigned by hub policy and sent as execution scope metadata.
- Worker reports resource-limit exits distinctly from task failures.
- Cleanup runs even on timeout/limit violation.

## 4) Fallback provenance metadata (ARCH-HUB-922)

When hub fallback occurs, provenance must be attached to resulting task records.

Recommended fields:

- `execution_mode`: `delegated` | `hub_fallback`
- `fallback_reason`: `no_worker` | `policy_denied` | `worker_unreachable` | `worker_failed`
- `delegation_attempted`: boolean
- `worker_candidates`: count

This metadata improves explainability and makes governance review straightforward.

## 5) Structured retry with bounded backoff (REL-RETRY-931)

Delegation retries should follow a bounded exponential backoff strategy with jitter.

Suggested model:

- Max attempts configurable per policy.
- Base delay + exponential factor + random jitter.
- Retry only transient failures (network timeout, temporary unavailability).
- No retries on policy denials or permanent validation errors.

Observability:

- Log retry count and final disposition.
- Retain timeline of attempts under the same trace id.

## Rollout strategy (additive, non-breaking)

1. Introduce config defaults behind existing settings surface.
2. Keep legacy task APIs unchanged.
3. Add optional metadata fields in responses and trace records.
4. Enable stricter policies gradually per environment.

This keeps compatibility while improving safety, reliability, and operator visibility.
