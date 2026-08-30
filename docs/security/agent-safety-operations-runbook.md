# Agent Safety Operations Runbook

## Readiness

1. Confirm the process role is `hub`.
2. Confirm `ANANTA_AGENT_SAFETY_STATE` points to durable storage.
3. Check `/api/agent-safety/overview`; `containment_available` must be true for
   a release profile.
   For Docker containment, set `ANANTA_AGENT_SAFETY_RUNTIME_ADAPTER=docker`
   and an exact comma-separated `ANANTA_AGENT_SAFETY_MANAGED_SANDBOXES`
   allowlist. `*` is invalid.
4. Create a revisioned policy. Adversarial targets must use exact `local:*`
   identifiers and bounded parallelism.
5. Register every Agent with a unique run, sandbox and group binding before it
   may execute.

An unavailable adapter is not a warning-only condition. The Hub can retain and
inspect state, but any stop operation produces `failed_closed`, and the run
remains non-executable.

## Automated incident flow

1. A signed sentinel or external detector appends an immutable Safety Event.
2. The Hub resolves the configured stop scope.
3. It requests freeze/terminate, egress deny and credential revocation.
4. It marks all selected runs non-executable regardless of receipt success.
5. It creates a bounded, redacted incident bundle.
6. Automated analysis supplies one or more closed root-cause classes and a
   disposition.
7. Replays remain denied until a patch digest or isolated retry disposition is
   present.
8. Exact and mutated local replays must pass before a new policy revision is
   eligible for rollout.

No step waits for a human. Missing evidence, adapters or dispositions return a
stable denial immediately.

The Control Center can submit a new policy revision through the same admin API.
The Hub's automatic preauthorization policy records its digest-bound decision;
unsafe combinations are denied immediately and never become pending approval.

## Freeze and cleanup

Freeze preserves state and blocks execution. It is not cleanup. Retention must
respect the policy TTL and storage limits. Cleanup is allowed only after the
incident bundle exists or the bounded retention expires. Termination also does
not delete the immutable ledger.

With the Docker adapter enabled, the Hub captures a content-reduced snapshot
before it stores the incident. The lifecycle-owned retention reconciler sweeps
expired freezes at a bounded interval and removes only exact allowlisted
sandboxes that already have an incident bundle. `/retention/sweep` exposes the
same bounded operation for deterministic automation and diagnostics.

## Trigger rotation and training

- use separate random symbol sets for train and holdout;
- never include the opaque trigger's real effect label in model-facing records;
- bind dataset and policy digests to the training job;
- compare open and opaque triggers per model/profile;
- report trigger, containment and self-report counts separately;
- never promote mock or test-double output as runtime release evidence.

## Failure handling

| Condition | Required result |
| --- | --- |
| Invalid or cross-run manifest | reject; do not call containment adapter |
| Duplicate trigger | idempotency conflict; run stays fenced |
| Missing sandbox adapter | `containment_failed_closed` plus incident bundle |
| Egress or credential receipt negative | selected run(s) remain non-executable |
| External replay target | reject immediately |
| Missing patch disposition | `agent_safety_patch_before_retry` |
| Missing `SRC_*`/`RUN_*` release evidence | keep release unverified |

## Recovery and rollback

Restart reopens the SQLite revision store but never an execution-fenced run.
Rollback selects an earlier policy for future admission only. It does not edit
historical events, operations, incidents or replays. If containment cannot be
verified after restart, keep the run blocked and repeat the external operation
through the Hub.
