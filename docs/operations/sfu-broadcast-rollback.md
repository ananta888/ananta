# SFU Broadcast Rollback Runbook

## Gate status

| Field | Value |
| --- | --- |
| Gate | `SFB-GATE-010` |
| Decision | **BLOCKED** |
| Rollback readiness | Unproven |
| Evidence state | No real rollback game-day evidence has been recorded |

This runbook defines the safe rollback contract for SFU broadcast. It is not
evidence that rollback works in a real deployment. Release cannot advance while
rollback commands, ownership, timeouts, compatibility, or recovery evidence are
unknown.

## Rollback invariants

- Rollback stops or reduces new admission before changing active runtime state.
- The hub remains the single control-plane authority.
- Workers do not initiate peer orchestration or independent recovery loops.
- Rollback never widens publication, subscription, replay, recovery, or administrative rights.
- Authentication, E2EE, privacy, and credential validation are never disabled.
- No room, grant, audit event, accounting record, or evidence is destructively deleted.
- Legacy API and client behavior remains compatible with the restored version.
- Active sessions are preserved when safe, otherwise drained with an explicit bound and reason.
- Every action is idempotent or has a recorded reconciliation step.

## Rollback authority and records

| Role | Required action |
| --- | --- |
| Incident commander | Declares rollback, severity, scope, and maximum recovery time |
| Release owner | Executes the recorded feature-flag or release-state operation |
| Operations owner | Stops admission, drains runtimes, restores versions, and verifies resources |
| Security owner | Verifies no rights widening, stale access, credential, privacy, or E2EE regression |
| Observability owner | Preserves evidence and reconciles SLO, routing, media, and accounting state |

The evidence record contains real names, exact commands or endpoints, responses,
timestamps, immutable digests, timeout outcomes, and approvals. This document
uses role names because inventing deployment identities or APIs would create
unsafe instructions.

## Rollback triggers

Rollback is mandatory when any configured threshold is exceeded or when a
security invariant fails. It is also mandatory for unknown or contradictory
state when safe containment cannot be proven.

| Trigger | Immediate containment | Target state |
| --- | --- | --- |
| Authorization, privacy, or E2EE regression | Stop new admission and disable affected feature scope | `flag-off` |
| Split brain or conflicting authoritative routes | Freeze routing mutations and isolate non-authoritative instances | `flag-off` after reconciliation |
| Capacity exhaustion or retry storm | Stop new admission and apply last verified lower cap | Last verified stage or `flag-off` |
| TURN pool or credential failure | Stop affected all-TURN admission; preserve unaffected paths only if policy allows | Last verified safe stage |
| Hub, SFU, Redis, database, or network recovery failure | Stop stage advancement and contain affected failure domains | Last verified safe stage |
| SLO or error-budget breach | Stop admission growth and revert the current stage | Previous verified stage |
| Legacy compatibility regression | Disable SFU broadcast for affected scope | `flag-off` unless a verified compatible scope exists |
| Missing or stale telemetry/evidence | Freeze changes; do not infer health | Last independently verified stage |

Security incidents default to `flag-off`; availability does not justify keeping a
possibly unsafe stage active.

## Stage rollback map

| Current stage | Owner | Command or endpoint | Successful target | Required evidence |
| --- | --- | --- | --- | --- |
| `flag-off` | Release owner | Recorded read operation confirming feature disabled | `flag-off` | No admissions, legacy path healthy, state reconciled |
| `internal` | Release owner | Recorded operation removing the internal allowlist or disabling the flag | `flag-off` | Allowlist no longer effective; sessions safely preserved or drained |
| `cohort` | Release owner | Recorded operation removing cohort routing and disabling new cohort admission | `internal` or `flag-off` | Non-cohort and cohort isolation, route convergence, no stale access |
| `percent` | Release owner | Recorded operation returning to the last verified percentage or zero | Previous verified stage | Percentage read-back from every authority; no version/route conflict |
| `released` | Release owner | Recorded feature-disable operation followed by compatible version/config restoration | Last verified stage or `flag-off` | Compatibility, security, capacity, routing, and accounting recovery |

The exact operation is deployment-specific and must be approved and captured
before rollout. Absence of an approved rollback command or endpoint blocks the
forward stage; operators must not improvise one during an incident.

## Bounded rollback procedure

1. Incident commander declares rollback, scope, severity, target stage, and deadline.
2. Observability owner freezes and preserves logs, metrics, traces, audit state, and digests.
3. Release owner prevents stage advancement and stops or reduces new admission.
4. Release owner applies the recorded feature-flag or release-state rollback operation.
5. Operations owner reads state from every hub and confirms one authoritative version and route.
6. Operations owner drains incompatible SFU or TURN instances within the configured timeout.
7. Operations owner restores the last verified compatible images and configuration when required.
8. Security owner verifies authentication, authorization, E2EE, privacy, credential, revoke, and stale-access behavior.
9. Observability owner reconciles hub, queue, database, SFU, TURN, room, and client accounting.
10. Release owner verifies legacy clients, legacy APIs, ordinary fallback, and direct peer-pair behavior.
11. Incident commander keeps the target stage blocked until recovery evidence is complete.

Each step records its precondition, exact command or endpoint, owner, start and
finish time, bounded timeout, result, abort condition, and recovery action. A
timeout is a failed step, not an implicit success.

## Abort and escalation rules

Abort the current rollback method and escalate containment when:

- A command affects a wider scope than approved.
- More than one hub claims authority or state read-back disagrees.
- A drain exceeds its bound or creates uncontrolled reconnect/retry load.
- The restored version cannot read current state without destructive migration.
- Authentication, E2EE, privacy, or least-privilege behavior cannot be proven.
- Accounting cannot distinguish preserved, drained, lost, duplicate, or stale sessions.
- Required telemetry or audit persistence is unavailable.

Escalation does not permit destructive deletion or rights widening. Prefer
feature disablement, stopped admission, isolation, and preservation of evidence.
Database or state repair requires its own reviewed recovery procedure.

## Recovery acceptance

Rollback is complete only when all of the following are supported by real,
fresh evidence:

- The target stage is read back consistently from every control-plane instance.
- There is at most one authoritative route and version for each room.
- New admission matches the target stage and capacity profile.
- Active sessions are accounted for as preserved, drained, or explicitly failed.
- No unauthorized, stale, duplicate, orphaned, or cross-room access remains.
- E2EE, rekey, credential rotation, revoke, and recovery behavior remains enforced.
- Hub, SFU, TURN, queue, database, and client accounting reconciles within configured bounds.
- SLOs and resource trends remain inside their configured bounds for the full recovery window.
- Legacy clients, APIs, ordinary fallback, and direct peer pairs remain compatible.
- The incident record and immutable evidence bundle are complete.

Unknown results keep the rollback open and the rollout blocked. Recovery of an
aggregate metric cannot conceal a failing security, region, version, transport,
browser, or client slice.

## Current conclusion

Rollback readiness for `SFB-GATE-010` is **BLOCKED**. No real incident exercise,
bounded rollback, compatible restoration, or recovery observation has been
recorded. The rollout must remain at `flag-off`.
