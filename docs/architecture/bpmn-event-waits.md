# Hub BPMN event waits: integration contract v1

This is the integrated contract for BPMNX-009/010: bounded intermediate
timer/message catches, XML admission, signed Hub message commands, and Native
checkpoint recovery. `bpmn_events_v1` is advertised by the production Native
selection composition only when `ANANTA_BPMN_EXECUTION_ENABLED` is enabled.
The static capability matrix describes support and its restrictions, not live
activation or permission to execute. Other runtimes do not advertise events.
Local synthetic test results are technical observations, not Hub-registered
production release evidence. Recipient fencing is integrated for waits, events,
checkpoints, Task admission and result acknowledgement. Remaining mutation and
operational gates below still block production admission; no deployment is implied.

## Composition and authority

Use `BpmnEventWaitService` from `agent.services.bpmn_event_wait`, the value
contracts in `bpmn_event_wait_contracts`, and `CheckpointEventWaitStore` from
`bpmn_event_wait_store`. All callers and persistence are Hub-side. No method
creates Tasks, dispatches Workers, starts threads, sleeps or invokes tools.

Construct one service per authoritative `WaitRunBinding(tenant_id, project_id,
workflow_id, run_id, definition_revision, plan_hash, policy_version)`, injecting
an epoch-seconds `clock`, the current Hub ownership `fencing_token`, and an
`EventWaitStore`. Bindings come from verified Hub run/plan state, never request
metadata. This is a workflow run identity; the service does not issue or verify
Evidence Registry identities.

`CheckpointEventWaitStore(checkpoints, key_ring)` reuses `CheckpointStore`,
`SignedCheckpoint`, `WorkflowState` and their signing/verification ports. The
reserved checkpoint task namespace is **`bpmn-event-waits:v1`** and runtime
namespace is `hub-bpmn-event-waits`, version `1`. Native's control task MUST
use a different identity. One signed aggregate per tenant/run contains all
waits, inbox dedupe tombstones and wakeup receipts. Project, definition and
policy bindings are checked on every load; changing a plan/revision in place
fails closed. A newer ownership fence fences out older service instances.

`EventWaitStore.load` returns detached state and revision/fence;
`commit` atomically compares the revision and rejects stale fences. The
service retries compare-and-swap conflicts at most `WaitLimits.cas_attempts`.
Other storage/signature errors propagate without a fallback. The existing
SQLite and in-memory checkpoint adapters are exercised by the component
tests. In-memory composition is test-only. Production composition must prove
durable shared storage, enforce finite database timeouts, inject signing keys
and retain verification keys for live runs. No keys are read from environment
or files by these new components. SQLAlchemy over SQLite is also exercised by
the isolated container suite; PostgreSQL concurrency is not established here.

### Run lease and atomic recipient fencing

`BpmnRunLeaseService` acquires a signed CAS lease in the separate
`bpmn-control-lease:v1` checkpoint namespace for each Native start, advance or
resume operation. It binds the tenant, workflow, run, plan, policy and control
task, includes an owner and bounded expiry, increments the ownership fence on
acquisition, and releases only its own lease. Native passes this lease through
`state.control_lease`; the lease itself is not serialized as Native business
state. Native checkpoint persistence uses the loaded revision and the control
lease fence. Worker-attempt fences and caller-provided constants are never the
event adapter's ownership authority.

The adapter requires `guard=lease.ensure_valid` and
`fencing_token=lease.fencing_token`. The wait service calls this guard before
reading and immediately before every commit, including CAS retries and
idempotent returns. A lost lease propagates immediately. Wait aggregate CAS
continues to reject stale persisted fences. Native checks ownership again
before checkpointing, appending consumed events and delegating work.

Guard lookups alone are not atomic. The integrated wait adapter therefore requires
`CheckpointEventWaitStore.for_lease(lease)` and `save_fenced`; there is no unfenced
fallback. SQLite validates the signed current lease inside `BEGIN IMMEDIATE`.
SQLAlchemy locks an immutable lease anchor and retains it through the recipient
write; lease acquisition/release uses that same anchor. Event append, Native
checkpoint and actual Task ingestion use their corresponding fenced recipients.
Adversarial tests pause within recipients, supersede the lease, and require zero
stale writes. SQLAlchemy/SQLite timer/message cases also run in separate Hub/Worker
containers with SQL recomposition.

This is per-recipient fencing, not a distributed cross-store transaction.
PostgreSQL concurrency, all remaining grant/claim/side-effect/cancellation
recipients and full dispatch recovery are not claimed complete; see the
[completion review](../security/bpmn-completion-review.md).

## Native adapter hooks and plan contracts

`NativeGraphOrchestrator` accepts an injectable `bpmn_events` adapter and
defaults to `BpmnNativeEventRuntime(store=CheckpointEventWaitStore(...), clock=...)`.
Each operation receives keyword-only `plan`, `request`, `state`,
`fencing_token`, `guard`, and a zero-argument `persist()` callback to
`_save_checkpoint(plan, request, state)`:

- `arm(..., node=node) -> WaitView` registers an activated catch.
- `reconcile(...) -> tuple[Wakeup, ...]` applies receipts before dispatch and
  returns stable consumed receipts for deduplicated event append, including replays.
- `deliver_message(..., command=verified_command) -> MessageDelivery` admits
  only the separately verified and policy-authorized `bpmn_message` command.
- `cancel_run(...) -> tuple[WaitView, ...]` closes the durable wait aggregate.

Native checkpoints retain `bpmn_waits`, mapping node IDs to exactly
`activation_id`, `definition_hash`, `wakeup_id`, and `bpmn_applied_wakeups`.
Activation identity is deterministically bound to the effective plan, run and
activation-expanded execution node. No evidence identity is minted. Deadlines,
inboxes and consumed receipts remain solely in the existing signed wait store.
The plan supplies `bpmn_definition_hash` and a canonical project ID in
`metadata.project_id` or `metadata.workflow_rollout_scope.project_id`; missing
scope fails Native validation before run creation.

Execution nodes use `node_type="bpmn_wait"` and `metadata.bpmn_wait`:

```json
{"kind":"timer","timer":{"duration_seconds":5},"timeout_seconds":20}
```

`timer` may instead contain only `due_at` (UTC epoch seconds). A message
definition contains exactly `kind="message"`, `name`, `correlation_key`,
`schema_id`, `payload_schema`, and `timeout_seconds`. The schema ID is the
canonical SHA-256 of the embedded schema. `validate_event_definition` and
`validate_event_node` reject extra semantics, worker tools/artifacts, side
effects and gates on wait nodes. Plans containing catches require
`bpmn_events_v1`; moving wait metadata onto a task is rejected.

## Native tick and recovery

1. Persist a catch intent in Native's signed `bpmn_waits` projection before
   registering it. Include element ID, Hub-owned activation ID (distinct for
   each iteration) and the immutable definition digest. Configuration is pinned
   in the signed plan instead of copied into another wait-state machine.
2. Call `arm_timer(activation, TimerSpec(...), timeout_seconds=...)` or
   `subscribe_message(activation, MessageContract(...), timeout_seconds=...)`.
   Repeating the same registration returns the current wait without resetting
   its deadline. Different configuration for the same activation is rejected.
   Persist the returned `wakeup_id`. If Native crashes between checkpoints,
   replay the intent to recover the same wait and ID.
3. Keep the workflow `running` with that step marked waiting. Native reconciles
   registered intents each tick before delegation. Waiting catches are excluded
   from dispatch candidates without occupying worker slots. Never keep a
   request/thread blocked awaiting a timer/message. Active-run enumeration
   stays with the Hub.
4. Under existing authoritative run ownership, call
   `consume(activation, wakeup_id=...)`. A ready wait becomes `consumed`; an
   expired/cancelled/not-ready wait returns `None`. A mismatched ID raises.
   A successful receipt contains run, target, kind, ready/consumed timestamps
   and optional message ID/data. Repeating consumption returns the identical
   receipt, including after its original deadline or process restart.
5. Revalidate Native run status, definition and activation, then persist the
   applied `wakeup_id` with the step transition in Native's checkpoint and
   use the existing idempotent successor/task dispatch path. On recovery,
   replay `consume` for every registered, unapplied intent, even when
   `pending()` is empty: consumed receipts deliberately leave the pending set.
   Treat two concurrent returns of the same receipt as one logical transition.

There is no transaction spanning the wait checkpoint, Native checkpoint and
task queue. The parent MUST serialize receipt application with run cancellation
under the Hub run owner and retain applied wakeup IDs with idempotent dispatch.
Do not dispatch merely because `pending()` returned a notification. This
component guarantees one durable consumption decision, not exactly-once
external side effects.

`cancel(activation)` atomically cancels waiting/ready catches; an unknown
activation raises instead of pretending a cancellation was persisted. A
consumed catch stays consumed. `cancel_run()` persists a closed-run tombstone,
including for an empty run, cancels waiting/ready catches and buffered messages,
and rejects new activations/messages. The adapter checks `is_closed()` before
applying receipts: a durable cancellation tombstone wins even if Native still
says running after a crash. Native cancellation/failure closes waits, and retry
on an event-bearing failed/cancelled run is denied. Start a new run instead.

## Timer semantics

Only a one-shot intermediate catch is represented. `TimerSpec` accepts exactly
one of `duration_seconds` (finite, nonnegative) or `due_at` (positive finite UTC
epoch seconds). `TimerSpec.at(iso_date)` requires an explicit timezone offset
or `Z` and normalizes it to a UTC instant. Naive dates and recurring expressions
are rejected. `parse_event_definition(element, definitions_root)` in
`agent.visual_process.bpmn_event_definitions` lowers fixed ISO day/hour/minute/
second durations with bounded decimal precision, or strict timezone dates.
Calendar months/years/weeks and repeat expressions are rejected. The initial
maximum wait is 86400 seconds and a duration must fall strictly before it.

Relative time starts at the first successful wait registration. Retry/restart
does not shift it. Absolute dates already in the past become ready immediately.
`timeout_seconds` is mandatory, positive and bounded by `max_wait_seconds`;
the timer must fall strictly before this expiry. At `now >= expires_at`,
expiry wins over readiness/consumption, even if the timer became ready earlier.
Thus an overdue restart resumes only within its admitted deadline. Terminal
states cannot revive on backward clock movement. The persisted observation
time also prevents committed transition timestamps from moving backward.

## Authenticated message ingress

`InboundMessage` carries the complete `WaitRunBinding`, exact `CatchActivation`,
`MessageContract(name, correlation_key, schema_id)`, message ID, sent/expiry
timestamps and a JSON-object payload. The authenticated Hub Inbox adapter must
inject both `MessageAuthorizer.authorize(message)` and
`MessagePayloadValidator.validate(schema_id, payload)`; neither defaults to
allow. The authorizer must resolve the principal, tenant/project, active run,
immutable plan and exact permitted activation/contract from Hub authority.
For early messages the activation must already be reserved by the Hub.
Schema IDs resolve only to the immutable schema embedded in the admitted plan;
there is no external fetch. XML must reference an explicitly named top-level
BPMN `message`. Its catch's Ananta `bpmn_wait` metadata contains
`correlation_key`, `payload_schema`, and optional `timeout_seconds`. Schemas
are closed JSON objects with at most 32 named properties, `required`, and
`additionalProperties:false`. Property types are string, integer, number,
boolean or null; strings require `maxLength` between 1 and 4096. Nested objects,
arrays, `$ref`, other keywords, unknown fields and type coercion are rejected.

The authenticated visual-process route `POST /workflow/<workflow_id>/message`
accepts exactly the required outer fields `command_id`, `expected_revision`,
`plan_hash`, `step_id`, `payload`, plus optional `run_id`, `checkpoint_ref`.
Its `payload` contains exactly:

```json
{
  "activation_id": "<Hub catch activation from the run>",
  "message_id": "order-message-42",
  "name": "order.received",
  "correlation_key": "order-42",
  "schema_id": "<canonical admitted schema digest>",
  "sent_at": 1790245800,
  "expires_at": 1790245860,
  "payload": {"value": 7}
}
```

The Hub signs a NEW `bpmn_message` command with authenticated actor/tenant and
the exact run/plan/checkpoint/target bindings. Generic signal and approval
commands cannot substitute for this route. Native verifies the signature,
revision, replay identity and Hub policy before invoking the adapter; payload
actor, tenant, plan or routing authority is never accepted. Authorization is
checked on duplicates as well as initial ingress. Callback failures or
contract mismatches produce no inbox write. Business data is recorded only in
`state.node_results[node_id].payload`, never merged into control state.

Timestamps are finite absolute instants. Future-sent envelopes are rejected;
TTL is bounded by `max_message_ttl`. Expiry is inclusive. Once matched before
its TTL, the message has been consumed from the inbox and its data remains
available to the receipt until the catch's own consumption deadline. Payloads
are copied, size/depth/item bounded, pure JSON and subject to the existing
embedded-secret rejection. They are data only: Native must explicitly map them
to permitted business fields, never merge them into policy, tools, approval,
ownership, runtime metadata or task-routing authority.

Public Native ingress denies early messages. At the lower component level an explicit
`EarlyMessagePolicy(ttl_seconds, max_buffered)` bounds it; effective buffer
expiry is the earlier of envelope expiry and receipt time plus policy TTL.
Registration matches the earliest received unexpired message with the exact
activation/contract, tie-breaking by message ID. Other buffered messages remain
bounded and expire. There is no broadcast or wildcard correlation.

Message-ID dedupe is per bound tenant/run and fingerprints the entire envelope,
including target, revision, correlation, schema, data and timestamps. Changed
content under the same ID fails closed. A duplicate matched/buffered/expired
message cannot activate another catch. Distinct concurrent messages for a
single catch produce one winner; later deliveries fail `bpmn_wait_not_waiting`.

## Events, bounds and remaining integration gates

`consumed_receipt.to_event()` produces a stable `CanonicalWorkflowEvent`
(`workflow.bpmn.catch.consumed`) with `event_id == dedupe_key == wakeup_id`.
It carries binding metadata and a payload digest, never message data. The
parent appends it through the existing `EventStore` and its sequence/dedupe
contract. Receipt replay repairs a crash before event append. This projection
does not prove successor dispatch or workflow completion. Native must project
waiting/cancelled/expired step statuses as part of its own event lifecycle.

Aggregate size, payload size, activation/message counts, TTLs and CAS attempts
are bounded by `WaitLimits`. Counts include terminal tombstones for the entire
run; reaching a limit fails closed instead of discarding dedupe history. Store
history retention/archival is parent infrastructure policy, and must preserve
live-run tombstones and consumed receipts. No garbage-collection scheduler or
global due-time index is introduced here.

`tests/bpmn/test_native_event_integration.py` covers XML-to-request-to-plan-to-
Native execution with signed SQLite checkpoints, real bounded control leases,
and a synthetic replayable Worker queue. It checks signed messages, one-time
receipt application, cancellation, timer expiry, restart without deadline
drift, and crashes immediately before/after the applied Native checkpoint.
The successor submission assertion reads the durable applied receipt first.
This is component integration, not a deployed release gate. Wait/event/checkpoint
recipient fencing and SQLAlchemy/SQLite container composition are now integrated;
PostgreSQL, full dispatch recovery and remaining mutation recipients still need
separate acceptance. Start/boundary/cyclic timers, start/boundary/broadcast messages
and event subprocesses remain unsupported.

SOLID check: lifecycle logic, immutable contracts and persistence/signing are
separate modules (SRP); storage, clock, authorization and schema validation are
injected ports (DIP/ISP). Both tested persistence implementations use the same
CAS/fence contract (LSP). No new worker orchestration or global singleton is
introduced. The existing broad Native orchestrator retains its SRP debt;
its additions delegate wait lifecycle to the adapter rather than embedding
another inbox/timer implementation. The scheduler's optional waiting-node
exclusion preserves existing callers and worker capacity semantics.
