# Model-Intelligence Operator Runbook

## Scope

This runbook covers the content-free policy and observability ports introduced
by OWMA-017 and OWMA-018. The Hub remains owner of identity, policy, job state,
queueing, retention decisions, and artifact publication. Workers execute only
delegated immutable jobs. They do not schedule other workers and do not resolve
tenant access independently.

The ports are integration seams, not evidence that a dashboard, alert receiver,
Artifact Store deletion adapter, or production admission path is already wired.

## Mandatory safety invariants

- Authorize tenant scope before evaluating a role. Tenant administrators remain
  tenant-bound.
- Pass artifacts across boundaries only as
  `ananta.model-intelligence.artifact-ref.v1`; never log or persist container
  paths as retention coordinates.
- Never emit raw prompts, outputs, activations, hidden states, attention values,
  logits, model bytes, secrets, bearer tokens, or local paths.
- Use the HMAC correlation service with a deployment secret of at least 32
  random bytes. Do not use raw job, task, tenant, or artifact identifiers as
  metric labels.
- Evaluate tenant quotas before an expensive worker execution. A missing or
  inconsistent tenant snapshot fails closed.
- Artifact deletion is two-phase: plan `delete_pending`, invoke the existing
  Artifact Store port with the `ArtifactRef`, then confirm `deleted`. Repeating
  either transition must remain idempotent.
- Parser admission remains owned by OWMA-003. Do not duplicate or weaken its
  path, symlink, deserialization, remote-code, or archive limits here.

## Operational signals

| Metric | Kind | Meaning |
|---|---|---|
| `model_intelligence_jobs_total` | counter | Job transitions by bounded state and stable reason code |
| `model_intelligence_job_duration_seconds` | histogram | Completed worker execution duration |
| `model_intelligence_queue_depth` | gauge | Current bounded worker queue depth |
| `model_intelligence_resource_bytes` | gauge | Disk, RAM, optional VRAM, or artifact-byte observation |
| `model_intelligence_artifact_bytes_total` | counter | Published artifact bytes |
| `model_intelligence_quota_rejections_total` | counter | Pre-execution quota rejections |

Allowed job states are `queued`, `running`, `succeeded`, `failed`, and
`cancelled`. Reason codes are closed vocabulary. Unknown values must be rejected
rather than converted to high-cardinality labels.

## Quota decision order

The deterministic pre-execution order is tenant, disk, RAM, parallelism,
artifact bytes, then optional VRAM. Equality with a limit is accepted; only a
strict excess is rejected. Operators must not raise one resource limit merely
to conceal pressure in another dimension.

## Failure scenarios

| Scenario | Detection | Immediate action | Recovery verification | Expected evidence |
|---|---|---|---|---|
| Queue overload | `queue_full`, growing queue gauge | Stop new analysis submission for the affected tenant; preserve queued jobs | Queue falls below limit and a bounded canary job reaches `succeeded` | Sanitized job event, queue metrics, quota decision |
| Disk pressure | `disk_quota_exceeded` before execution | Stop artifact-producing jobs; do not delete unknown files by path | Retention reconciliation completes through ArtifactRefs and free-space probe is healthy | Quota decision, retention transitions, Artifact Store deletion receipts |
| RAM pressure | `ram_quota_exceeded` | Reject new reservations and allow bounded cleanup | Resource gauge returns below limit and no active lease is orphaned | Sanitized failure event and resource observations |
| VRAM pressure | `vram_quota_exceeded` | Reject GPU work; do not silently select an unapproved device | Adapter cleanup completes and a capability probe reports the intended device state | Quota decision, cleanup evidence, capability observation |
| Artifact quota | `artifact_quota_exceeded` | Stop publication before bytes are written | Tenant usage is reconciled and the next bounded publication succeeds | Artifact-byte accounting and ArtifactRef-bound transition |
| Timeout | `timeout` | Request cancellation and fence late completion | Job reaches one terminal state and no artifact is published after the deadline | Correlated Hub/Worker terminal events |
| Cancellation | `cancelled` | Preserve the Hub cancellation decision and ask the worker to clean up | Worker stops, leases are released, and retry requires a new Hub job | Correlated cancellation and cleanup observations |
| Worker crash | `worker_crashed` or lost health | Fence the task; do not let a worker self-reschedule | Hub recovery decides retry or failure and partial artifacts remain unpublished | Hub task transition, worker-health evidence, Artifact Store publication state |

## Retention and legal hold

Legal-hold records never transition to deletion. A user-requested deletion or
retention expiry from another tenant is denied before the Artifact Store is
called. Audit events contain only the ArtifactRef digest, transition state,
reason code, hashed idempotency key, and sanitized correlation.

## Container hardening gate

Production acceptance must separately demonstrate that the analysis worker runs
non-root, with a read-only root filesystem and no network route. This runbook
does not treat Compose declarations alone as runtime evidence. GPU and
platform-specific workers require their own built-container smoke.

## Evidence review

For each incident retain only content-free evidence:

- Hub job state and stable reason code
- Worker task state and stable reason code
- sanitized HMAC correlation
- quota snapshot totals and requested totals
- ArtifactRef digests and idempotent retention transitions
- health and capability observations

Any report containing raw prompts, model bytes, activations, secrets, tenant
identifiers, or container paths is invalid security evidence and must be
quarantined.

## Local persistence and metrics adapters

`SqliteModelIntelligenceRetentionAdapter` persists ArtifactRef-bound state and
transition history transactionally. Registering the same record and replaying
the same transition are idempotent. A conflicting tenant, ArtifactRef, or state
fails closed. Deleted records remain as content-free audit tombstones; the
adapter never resolves or deletes an artifact path.

`InProcessOpenMetricsAdapter` and `WorkerInProcessOpenMetricsPort` provide
dependency-free process-local counters, gauges, and histograms. Their
`render_openmetrics()` output can be scraped or bridged by deployment-specific
infrastructure. Process-local metrics reset on restart and are not a durable
audit store. Production deployments must therefore use retention transition
history and Hub job state as the durable operational record.

The restricted-inference Compose profiles provide the reusable local hardening
baseline: UID/GID `10002:10002`, read-only root filesystem, all Linux
capabilities dropped, `no-new-privileges`, bounded PIDs/CPU/memory, read-only
model mount, offline model flags, hardened tmpfs mounts, and only the internal
restricted-inference control network. This proves policy configuration, not
built-container runtime behavior; the separate runtime smoke remains required.

## Alert evaluation and delivery boundary

`ModelIntelligenceAlertEvaluator` evaluates queue, disk, RAM, optional VRAM,
artifact, failure, cancellation, and worker-crash thresholds without external
dependencies. It emits only aggregate ratios, counts, severity, stable reason
codes, and runbook anchors. Threshold evaluation is local and deterministic.

Collector persistence, alert routing, paging, acknowledgement, escalation, and
delivery-SLO evidence remain an external deployment gate. A local evaluation
must never be reported as proof that an operator received an alert.

## Built-container smoke

After building the intended worker image locally, run:

```bash
python scripts/model_intelligence_container_smoke.py \
  --image <already-built-image> \
  --expected-uid 10002
```

The script performs no build and no download. It starts the image with a
read-only root, `--network none`, dropped capabilities, no-new-privileges,
bounded PIDs, and hardened tmpfs mounts. The in-container probe verifies the
effective non-root UID, denied root writes, denied egress, cleanup of a partial
temporary artifact, and removal of the stopped container. A successful run is
runtime evidence for that exact image ID only.
