# Goal-First Security Defaults & Governance Visibility

This document defines secure-by-default behavior for goal workflows.

## Secure Defaults

- Least-privilege access to artifacts, traces and verification data.
- Team-scoped authorization for goal/plan/task resources.
- Default deny for advanced operational endpoints.

## Authorization Model

Recommended roles:

- `admin`: cross-team governance and policy administration
- `team_admin`: team-level management and audit visibility
- `team_member`: execute and view team tasks/artifacts
- `viewer`: read-only access to approved result summaries

## Governance Visibility

Every policy-relevant event should be inspectable:

- goal acceptance/rejection
- routing decision outcomes
- verification pass/fail transitions
- privileged trace access

## Audit Requirements

Capture:

- actor
- role and team scope
- resource identifiers (`goal_id`, `task_id`, `artifact_id`, `trace_id`)
- decision and reason
- timestamp and integrity metadata

## UX Guidance

- Keep simple views focused on approved artifacts.
- Place advanced policy and trace details behind explicit drill-down.
- Explain denied actions with non-sensitive policy reasons when possible.

## Operational Controls

- configurable retention for audit and trace records
- alerting on repeated denied access
- periodic review of privileged role assignments
