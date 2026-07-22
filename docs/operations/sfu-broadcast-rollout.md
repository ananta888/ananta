# SFU Broadcast Rollout Runbook

## Gate status

| Field | Value |
| --- | --- |
| Gate | `SFB-GATE-010` |
| Decision | **BLOCKED** |
| Release stage | `flag-off` |
| Evidence state | No real rollout game-day evidence has been recorded |
| Advancement | Forbidden until all prerequisite gates and current-stage checks pass |

This runbook defines a fail-closed staged rollout. It does not authorize a
production release. Unknown state, missing telemetry, missing ownership,
unrecorded commands/endpoints, incomplete evidence, or an expired prerequisite
keeps the feature at the last verified stage.

## Control-plane and safety invariants

- The hub remains the owner of orchestration, routing, policy, and task state.
- Workers execute delegated work and never coordinate peers directly.
- A rollout must not widen authorization, subscription, publication, replay, or recovery rights.
- E2EE and authentication requirements are never relaxed to preserve availability.
- Legacy APIs and legacy clients remain compatible throughout the rollout.
- Rollout and rollback do not destructively delete rooms, grants, evidence, accounting, or audit data.
- Existing sessions are drained or preserved according to the verified compatibility policy; they are not killed solely to simplify rollout.
- Every mutating operation has one named role owner and one incident commander.

## Required roles

| Role | Responsibility |
| --- | --- |
| Release owner | Owns stage transitions and records the exact command or endpoint |
| Operations owner | Owns runtime health, capacity, networking, SFU, and TURN checks |
| Security owner | Owns authorization, E2EE, privacy, credential, and stale-access checks |
| Observability owner | Confirms SLO inputs, metric completeness, alerts, and accounting reconciliation |
| Incident commander | Can stop advancement and order rollback without additional approval |

A role may be held by the same person only when the deployment's separation-of-
duties policy permits it. The real names and contacts are recorded in the
game-day evidence, not guessed in this document.

## Evidence required before any stage change

The release owner must record all of the following:

- Immutable source, image, configuration, schema, and infrastructure digests.
- Exact deployment-specific command or endpoint, parameters, actor, and response.
- Current and target stage, cohort or percentage, and affected failure domains.
- Preconditions and their fresh evidence references.
- SLO and error-budget values before, during, and after the observation window.
- Authorization, E2EE, privacy, stale-access, and accounting checks.
- Start time, timeout, observation window, abort reason, and recovery result.
- Legacy-client and legacy-API compatibility results.
- Approval by every required owner for the next stage.

Placeholders, assumptions, dashboard screenshots without underlying queries, or
unknown values are not acceptable evidence.

## Stage sequence

Stages are strictly ordered:

`flag-off` -> `internal` -> `cohort` -> `percent` -> `released`

No stage may be skipped. A successful observation in one region, failure domain,
transport mode, or client class does not imply success in another.

| Stage | Owner | Preconditions | Command or endpoint | Required evidence | Timeout and observation | Abort | Recovery |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `flag-off` | Release owner | Deployable artifacts are digest-bound; legacy path verified; all required gates current | Deployment-specific feature-flag read/write operation recorded before execution | Flag is off everywhere; no SFU broadcast admission; legacy behavior unchanged | Profile-defined bounded check and observation window | Any unexpected admission, config drift, or missing telemetry | Force flag off, stop new SFU broadcast admission, reconcile state |
| `internal` | Release owner | `flag-off` evidence passes; internal identities and rooms are explicitly allowlisted | Recorded feature-flag operation targeting the internal allowlist only | Real media, all-TURN, authorization, E2EE, rekey, recovery, and accounting checks for internal traffic | Profile-defined bounded window across required failure domains | Any SLO, security, privacy, compatibility, or accounting failure | Remove internal allowlist, stop admissions, preserve or drain sessions safely |
| `cohort` | Release owner | Internal stage passes; cohort definition is immutable and least-privilege | Recorded cohort-routing or feature-flag operation with exact cohort digest | Cohort membership, non-cohort exclusion, capacity headroom, fault response, legacy compatibility | Profile-defined bounded cohort window | Cohort leak, rights widening, first capacity failure, unknown health | Return to internal or flag-off according to incident severity |
| `percent` | Release owner | Cohort stage passes; percentage and regional distribution are approved | Recorded rollout operation with exact configured percentage and failure-domain allocation | Per-version, per-region, per-transport, and per-client SLOs; error budget; capacity reserve; rollback readiness | Profile-defined bounded window at each approved percentage | Any stop threshold, skew, unexplained variance, or missing slice | Set percentage to the last verified value or zero; execute rollback runbook |
| `released` | Release owner | Every configured percentage passes; no open blocking incident; rollback remains tested | Recorded operation setting the release state for the explicitly approved scope | Full-scope SLOs, security invariants, compatibility, accounting, capacity, and audit trail | Profile-defined post-release observation window | Stop threshold, regression, or evidence becoming incomplete/stale | Disable new admissions or feature, then execute rollback runbook |

The exact command or endpoint is deployment-specific and must be captured before
execution. This runbook intentionally does not invent a production API. If no
approved command or endpoint is recorded, the step cannot begin.

## Stage decision procedure

1. Confirm all required prerequisite gates are current and passing.
2. Confirm owner assignments, incident channel, and rollback authority.
3. Capture the current stage and independently read it back from every control-plane instance.
4. Verify health, capacity reserve, SLO inputs, security invariants, and legacy compatibility.
5. Record the exact mutation command or endpoint and its bounded timeout.
6. Apply one stage transition only.
7. Observe for the full configured window without hiding failed samples or retries.
8. Reconcile hub, SFU, TURN, database, queue, and audit state.
9. Approve the next stage only when every required owner signs complete evidence.

An unknown, stale, contradictory, or partially unavailable control-plane state
is a stop condition. The system remains at, or returns to, the last verified
stage. Availability pressure is not permission to advance.

## Stop conditions

The stage stops immediately when a configured SLO or error threshold fails, or
when any of these conditions is observed:

- Authorization or E2EE downgrade, rights widening, or cross-room data exposure.
- Stale access beyond its bound, invalid credential acceptance, or replay acceptance.
- Split brain, more than one authoritative route/version, or routing during a forbidden partition.
- Hub, SFU, TURN, Redis, database, or network failure outside the verified recovery budget.
- Rekey, join, layer-switch, recovery, or control interruption outside its budget.
- Retry storm, uncontrolled resource growth, OOM, or failure to degrade before a hard limit.
- Unexplained accounting difference among hub, SFU, TURN, and client observations.
- Regression for legacy APIs, legacy clients, ordinary fallback, or direct peer pairs.
- Missing metrics, failed probes, stale dashboards, or unknown release state.

No single aggregate may mask a failing region, version, transport mode, browser,
cohort, or security slice.

## Required game-day incidents

Before release approval, the real game day must exercise and record at least:

- SFU runtime loss and restart during active fanout.
- Hub loss and restart without creating a second authority.
- Network partition, stale heartbeat, clock skew, and rolling version skew.
- Redis or cluster-control failure and database failover.
- TURN pool loss, credential rotation, relay exhaustion, and all-TURN recovery.
- Rekey, revoke/rejoin, stalled receiver, burst, and recovery behavior.
- Capacity stop at the first failed tier and safe admission degradation.
- Telemetry loss, conflicting telemetry, and evidence-collection failure.

Every incident record includes the triggering action, expected containment,
observed result, timeout, abort decision, rollback action, recovery evidence,
and responsible owner. A partial or simulated incident does not satisfy a real
game-day requirement.

## Current conclusion

`SFB-GATE-010` is **BLOCKED** at `flag-off`. No real staged rollout, incident
exercise, or rollback evidence exists. Advancement to `internal` is not approved.
