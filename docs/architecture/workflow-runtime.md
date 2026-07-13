# Production Workflow Runtime Architecture

## Outcome

Ananta can execute one versioned, runtime-neutral `ExecutionPlan` through its
Native graph runtime, a LangGraph worker adapter or optional durable Temporal
infrastructure. The runtime changes execution mechanics, not ownership:

```text
Goal -> Hub plan/policy -> Hub task queue -> delegated worker step -> evidence
             |                                      ^
             +-> optional Temporal durability ------+
```

The Hub remains the sole control plane and owner of planning, routing, policy,
approvals, budgets, tasks, side-effect decisions and canonical read models.
Workers execute one delegated step and do not orchestrate other workers.
Temporal schedules deterministic workflow/Activity code; an Activity submits
work to the Hub rather than selecting a worker. LangChain is not a runtime or
control plane here: it may only implement a provider, retriever or tool adapter
behind the same policy boundary.

This document complements the older
[workflow backend compatibility interface](workflow-backend-interface.md). The
compatibility interface remains available for existing callers; new workflow
features use the runtime-neutral contracts and Hub control service.

## Responsibility map

| Component | Owns | Must not own |
| --- | --- | --- |
| Hub control service | identity, plan validation, runtime selection, commands, history access | model/tool execution |
| Hub Native orchestrator | graph readiness, gates, fan-out bounds, deterministic merge, checkpoints | executing a task node in-process when it can delegate |
| Hub task queue | delegated task lifecycle and worker assignment | runtime-specific graph state |
| Worker runtime | one signed delegated node, provider/tool adapter calls, artifacts | task creation, runtime selection, worker-to-worker delegation |
| Temporal Server | durable workflow history, timers, Activity scheduling | Ananta users, policies, task queue or worker selection |
| Temporal worker/Activity | deterministic technical workflow and Hub gateway call | direct Ananta worker execution or policy decisions |
| Angular | authenticated Hub read models and approved Hub commands | direct worker/Temporal access or success inference |

## Runtime-neutral contracts

`ExecutionPlan` contains only domain contracts: tenant, plan/workflow identity,
policy version, required capabilities, nodes, declarative edges, gates,
artifacts and budgets. It contains no LangGraph object, Temporal handle, provider
client or callable. Its canonical hash binds authorization, checkpoints and
release evidence.

Every executable node becomes a delegated request with:

- tenant, workflow, run, step and attempt identity;
- plan hash and policy version;
- ownership fencing token;
- signed, expiring authorization envelope;
- input artifact references and bounded parameters.

Results contain status, matching identities/fence and artifact references.
Large content and secrets never cross this contract; secrets stay external
references and content stays in tenant-authorized artifact storage.

Canonical events and signed checkpoints are the interoperability boundary.
Runtime-private history may provide more detail, but the Hub projection is the
only runtime-neutral operational read model. A completed runtime-private state
without matching canonical evidence is degraded, not verified success.

## Ports and dependency direction

Small interfaces protect the runtime boundary:

- `ExecutionRuntimePort`: validate a plan and execute one delegated step;
- `StreamingRuntimePort`: optional bounded event streaming;
- `CheckpointRuntimePort` and `ResumableRuntimePort`: optional checkpoint and
  resume behavior;
- `DurableRunInfrastructurePort`: start, describe, signal, cancel and history
  for durable infrastructure;
- Hub task, event, checkpoint, ownership, side-effect ledger, provider and
  artifact ports.

This is an SRP/ISP/DIP boundary: orchestration, persistence, transport and
execution remain separate; optional runtimes substitute through capabilities
instead of framework imports in the Hub domain. Composition roots may depend on
concrete SQL/Temporal adapters, while contracts and business rules do not.
Legacy `RunControlService` access is isolated behind a Hub command gateway for
compatibility; new UI and runtimes must not import it as a second control plane.

Native and LangGraph expose stream/checkpoint/resume through their Hub-owned
bridge and checkpoint gateway. Their worker `ExecutionRuntimePort` remains a
single-node validate/execute port and must not sign checkpoints, accept operator
commands or schedule work. This ownership split is deliberate ISP/SRP, not a
missing worker feature.

## Runtime behavior and capability truth

| Runtime | Execution mode | Typical capabilities | Important boundary |
| --- | --- | --- | --- |
| Native | Hub-owned resumable graph ticks plus delegated worker nodes | approval, bounded parallel, deterministic merge, checkpoint, resume, stream, subgraph | Hub performs graph decisions; workers execute one node |
| LangGraph | optional worker adapter | stateful graph task, human-in-loop, checkpoint where configured, streaming where supported | framework stays in worker adapter; no Hub orchestration moves into LangGraph |
| Temporal | durable infrastructure plus Hub-delegating Activities | durability, timers, finite retry, cancel, signal/update, history, replay | Temporal owns technical history only; Activity returns to Hub task gateway |

Capabilities are reported facts backed by contract-hash-matching release
evidence. `unsupported` is `incompatible`; it is not a degraded success. The Hub
selects deterministically from `preferred_runtime`, `allowed_runtimes`, required
capabilities, policy, data locality, budget, health and explicit fallback
policy. It records selected and rejected alternatives with reason codes.

A fallback is allowed only when explicitly enabled and semantically equivalent,
with no lost capability. Authorization, policy, audit, durability, resume and
side-effect guarding are protected capabilities. Missing a safe runtime results
in `blocked`/`incompatible`, never an implicit local fallback.

## State and lifecycle

The canonical lifecycle is reconstructed from events rather than mutable worker
memory:

```text
pending -> running -> waiting_for_approval -> running -> completed
                  \-> paused -> resumed ------^
running -> failed
running -> cancel_requested -> cancelled
running -> uncertain (operator reconciliation required)
```

At-least-once delivery is normal. Event dedupe, monotonic sequence, checkpoint
revision CAS, ownership leases/fences and a stable side-effect operation ID make
recovery deterministic. A retry budget is shared across Temporal, Hub task,
worker, tool and provider attempts. Non-idempotent operations are not blindly
retried; timeout after possible execution becomes `uncertain`.

Approval, edit, pause, resume, reject and cancel enter through authenticated
Hub commands. Commands bind actor, tenant, run, step, checkpoint, plan and policy,
expire, and carry a one-use nonce. Runtime-specific signals are adapters for
those Hub commands, not public control APIs.

## Persistence, recovery and projection

Production stores use transactional tenant-bound implementations for canonical
events/outbox, signed checkpoints, ownership/retry state and the side-effect
ledger. SQLite supports local/quickstart use; PostgreSQL is the production
default. Container-local memory is never the recovery source.

Temporal history is projected incrementally into canonical events. Cursor,
mapping version and consistency survive Hub restarts. Gaps, token loops, unknown
versions or binding conflicts produce `stale`/`inconsistent`; an upgraded
mapping requires deterministic full rebuild and comparison. Raw Temporal
payloads remain in Temporal and are represented by protected references.

Checkpoints are runtime-specific implementation state wrapped in a neutral,
signed envelope. Do not resume a Native checkpoint in LangGraph or transplant a
Temporal history to Native. Rollback routes new runs only; existing runs stay
pinned to the compatible runtime/build unless an explicitly tested migration
adapter exists.

## Provider, retrieval and tools

Provider invocation is mediated by tenant/policy/model/prompt-version context,
egress policy, redaction, deadlines, retry/token/cost budgets and bounded cache.
Retrieved context requires allowed tenant/source/scope/version provenance.
Missing or unknown source identifiers fail grounding and are never invented.

Tool calls pass registry schema validation, runtime authorization, policy,
budget, approval, ownership fencing and the side-effect ledger before the
invoker. The model cannot claim that an unregistered or denied tool ran. Tool or
provider exceptions after a possible write are recorded as `uncertain`.

## Observability and UI

Canonical telemetry links tenant-safe hashes, workflow/run/step/attempt,
runtime, operation, checkpoint and artifact references. It exports no prompts,
secrets or artifact bodies. Exporter failure cannot roll back canonical events.

OTLP export is optional (`pip install -e '.[observability]'`) and disabled by
default. `ANANTA_WORKFLOW_OTEL_ENABLED=true` requires an explicit
`ANANTA_WORKFLOW_OTEL_ENDPOINT`. Remote collectors require HTTPS; plain HTTP is
accepted only for localhost or the internal Compose service name
`otel-collector`. Collector headers are read only from the absolute JSON secret
file named by `ANANTA_WORKFLOW_OTEL_HEADERS_FILE`; they never belong in an
environment value, plan, event or span.

The canonical event store remains the source of truth. Export happens only
after a successful append and exporter failure is audited without changing the
event result. Attribute keys are fixed, values are bounded, tenant identity is a
short hash bucket, at most 32 attributes are emitted and payloads above 16 KiB
are replaced by digest/size metadata. Arbitrary payload keys must never become
labels; this bounds payload exposure and cardinality.

Angular consumes `/api/workflow-runtime/operations` from the Hub. It exposes
runtime, mode, capability truth, fallback, cost, latency, recovery, gates,
evidence, parity gaps and semantic deviations. Stale data and completed runs
without verified evidence are visibly degraded. Pause/resume/cancel/retry
commands require a current read model, bound approval, verified evidence and an
idempotency key.

## Runtime-neutral example

The example under [`examples/workflow-runtime`](../../examples/workflow-runtime/README.md)
uses one
[`execution-plan.v1.json`](../../examples/workflow-runtime/execution-plan.v1.json)
for all three runtimes. The Temporal selection adds durable infrastructure; it
does not change the plan. A deterministic offline provider fails the first
draft attempt, then returns fixed artifacts. The publication step pauses at an
approval and uses an idempotent side-effect operation. The standalone
walkthrough covers failure, approval, cancel, a real Temporal worker-container
crash and durable resume over `docker/compose-next` without a pytest runtime
dependency. Native and LangGraph use labelled deterministic example ports;
Temporal uses a real server, worker, workflow history and production HTTP
Activity gateway against a separate example Hub port.

The optional local live-provider overlay is deliberately outside the workflow
control path. It wraps one bounded OpenAI-compatible request in a LangChain
`RunnableLambda` provider adapter, reads its credential from a mounted file and
records only response metadata and a digest. It cannot schedule workflows,
create Hub tasks or own a queue.

Expected evidence is:

- canonical event stream and rebuildable Hub read model;
- signed checkpoint or protected Temporal history reference;
- stable side-effect ledger operation for the publish step;
- `draft`, `published` and `verification` artifact references;
- runtime evaluation with capabilities, deviations and recovery;
- the shared plan hash and explicitly classified example boundaries.

The generated artifact is example evidence with `production_release_gate` set
to `false`. Production promotion separately requires contract-hash-matching
release-gate evidence.

Core Native validation has no LangChain/LangGraph/Temporal import requirement.
The optional `lc-lg` extra supplies LangChain/LangGraph adapters; the `temporal`
extra supplies the pinned Temporal SDK; `observability` supplies optional
OpenTelemetry API/SDK/OTLP HTTP packages. The Compose images and exact commands
are documented in the
[example walkthrough](../examples/workflow-runtime/README.md) and
[rollout runbook](../operations/workflow-runtime-rollout.md).

## Security and operations

The normative boundaries and prevention/detection/audit/test mapping are in the
[workflow runtime threat model](../security/workflow-runtime-threat-model.md).
Production enablement, shadow rules, compatible rollback, backup/restore,
migration, key rotation and incidents are in the
[workflow runtime rollout runbook](../operations/workflow-runtime-rollout.md).
Temporal-specific replay, heartbeat and failure drills are in the
[Temporal runtime runbook](../operations/temporal-runtime.md).
