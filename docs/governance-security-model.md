# Governance And Security Model

This is the canonical document for governance and security requirements in the goal-first architecture.

It consolidates:

- `docs/goal-security-governance-visibility.md`
- `docs/security_baseline.md`

Artifact-specific lineage and response-shape guidance remains in:

- `docs/artifact-traceability-model.md`

## Scope

This model covers goal ingestion, planning, delegation/execution, verification, and artifact access controls.

## Core Principles

- Authentication: require authenticated requests for all mutation endpoints.
- Authorization: enforce team-scoped and capability-scoped access on goal/plan/task/artifact resources.
- Least privilege: default views return minimal fields; elevated detail requires explicit scope.
- Auditability: persist policy decisions, approvals, and execution provenance for review.
- Safe defaults: fallback/self-execution is opt-in and policy-governed.

## Role Model

Recommended role envelope:

- `admin`: cross-team governance and policy administration.
- `team_admin`: team-level management and audit visibility.
- `team_member`: execution and access to team-scoped work/results.
- `viewer`: read-only access to approved summaries.

## Governance Visibility

Policy-relevant events must be inspectable:

- goal acceptance or rejection,
- routing/delegation decisions,
- verification pass/fail/escalation transitions,
- privileged access to traces/artifacts.

Advanced policy and trace data should be available via explicit drill-down views, not as default payload noise.

## Required Audit Fields

Persist at minimum:

- actor identity,
- effective role and team scope,
- `goal_id` / `plan_id` / `task_id` / `artifact_id` / `trace_id` (as applicable),
- decision and reason,
- timestamp and integrity metadata.

## Baseline Controls

- Deny-by-default for privileged operational endpoints.
- Capability checks for plan edits, overrides, and sensitive artifact retrieval.
- Retention policies for audit and trace records.
- Alerting on repeated denied access and unusual privileged activity.
- Periodic review of privileged assignments.

## UX Guardrails

- Default UI focuses on approved user-facing artifacts.
- Governance internals stay operator-facing and intentionally discoverable.
- Denials should return actionable but non-sensitive policy reasons.

## Related Specifications

- Hub fallback, reliability, retries: `docs/hub-fallback-and-reliability.md`
- Execution constraints and envelopes: `docs/execution_scope.md`
- Artifact lineage and drill-down data shape: `docs/artifact-traceability-model.md`
