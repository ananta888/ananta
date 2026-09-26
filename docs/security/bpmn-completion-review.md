# BPMN completion security and recovery review

Scope: local synthetic tests and isolated Hub/Worker/browser acceptance on
2026-09-24. These are technical observations, not registered production release
evidence. No deployment or BPMN production flag is enabled.

## Verified integration

- Signed per-invocation control leases bind tenant, workflow, run, plan, policy
  and control task. They have finite expiry, CAS acquisition and monotonic fences.
  Process-exit takeover is tested independently of exception cleanup.
- Queue admission now uses the actual `TaskQueueService.ingest_task_fenced`
  and `TaskRepository.insert_native_task_fenced`. Lease validation, exact duplicate
  comparison and Task insertion share one authority-database transaction.
  The new path retains existing status policy and post-commit behavior; legacy
  unfenced ingestion is not a BPMN fallback.
- SQLite event/checkpoint/wait and SQLAlchemy event/checkpoint/Task recipients
  validate the locked current signed lease, not a separate pre-write read.
  SQLAlchemy lease revisions and recipient writes share an immutable anchor lock.
  Result acknowledgement and failed-attempt metering use fenced ownership
  recipients as well. Expiry/takeover injected inside these recipients denies
  stale mutations. The in-memory adapters are test-only.
- Exact persisted Task receipts recover admission after a queue/event/checkpoint
  interruption, with node/input/attempt/fence/plan/policy/control/correlation
  validation and current grant revalidation. No missing receipt is invented.
  A crash before admission may end in a bounded persisted failure.
- Cancellation also adopts admitted tasks missing from the Native checkpoint,
  then cancels/revokes them without dispatch. Already revoked receipts can be
  read for cancellation recovery only, never to reauthorize execution.
- Acknowledgement binds canonical result bytes, not just a caller result ID.
  Result scope, tool/artifact containment and finite nonnegative usage are checked
  before durable acceptance. Failed work consumes budget; exhausted runs persist
  failure before acknowledging or dispatching successors.
- Supplied stale checkpoints cannot substitute for the authoritative checkpoint.
  XML definitions, region projections, plan hashes, message targets and explicit
  command revisions remain bound. Graph recompilation is stable; delegated
  audit correlation comes from the Hub command, not a Worker-generated identity.
- Native catch-up accepts only known gateway observations, never an intervening
  graph control transition. Gateway audit-only appends have their own bounded
  sequence-conflict retry; exact replay retains the first event and changed
  dedupe content is denied.
- Synthetic Evidence Registry tests actually admit immutable sources and reserve
  test runs before simulated execution. Unknown, mutated, stale or synthetic
  identity claims cannot satisfy a production gate.

## Acceptance and limitations

The 21-case isolated container suite includes production queue, assignment,
Worker authentication, results, signed SQL state, explicit approvals/denials,
XOR/default, AND, bounded loop, embedded subprocess, timer/message catches,
SQL recomposition and public graph start/replay. Chromium uses the actual
Angular editor/API service and real production login route against that Hub.
A small fixture login form and synthetic release-admission port remain explicit.
No container shares the Hub database or signing key with a Worker.

The separate unit/browser-double suites cover adversarial boundaries and
editor races more broadly. Tests do not use skips/xfails to hide an unsafe
transition. Numerical results and exact scope are recorded in the active TODO.

## Still open — production admission remains blocked

1. **Artifact transfer/receipt admission.** Worker-local bytes and database IDs
   are not Hub receipts. The Recovery ingress contract cannot be reused for an
   ordinary Native node without its recovery-child authority bindings.
   Artifact-bearing BPMN plans now fail before execution with
   `bpmn_artifact_ingress_unavailable`. The container case is negative acceptance,
   not a successful transfer. A future narrow port must bind task, assignment,
   attempt, fence/dispatch lease, declared output, size/hash and immutable Hub
   bytes, with idempotent registration before completion.
2. **Generic result ingress.** `persist_forwarded_execution` still stores Worker
   result/terminal fields on a generic Task before Native validates attempt and
   fence. Hostile-result tests prove no canonical completion/acknowledgement,
   but do not claim the earlier Task write is authenticated admission.
   A lease-bound ingress recipient must validate before that write.
3. **Remaining mutation recipients.** Per-recipient fencing is not a distributed
   transaction. Ownership claim, grant creation/revocation, queue cancellation
   and side-effect transitions need a complete control-lease race audit and
   recipient integration. Existing attempt fences/CAS and caller checks remain;
   do not describe every mutation as atomically control-lease fenced.
4. **Operational/packaging evidence.** Full application boot, Autopilot dispatch
   and its durable outbox, container/database loss, PostgreSQL concurrency,
   production image packaging and locked-dependency frontend build are not
   established by this suite. No minipc test or production release was performed.
   CaseFlow trace composition and richer event/region property forms remain
   narrower UI integration work.

## SOLID check

Protected: SRP through separate XML compiler, input projection, event waits,
lease validation, receipt recovery, Task ingestion and runtime-provenance modules;
DIP/ISP through small optional fenced/read ports on existing boundaries.
LSP/OCP: legacy APIs remain additive and BPMN fails closed without required
recipients. No orchestration moves to Workers.

Preserved debt: the large Native orchestrator, gateway and Angular component
still coordinate multiple concerns. Neutral WorkflowRequest validation invokes
the separate BPMN compatibility facade; TaskQueueService still has transitive
route-ordering imports. Further decomposition should use bounded domain ports,
not a replacement runtime or an independent queue.
