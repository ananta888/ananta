# ADR: Workflow Runtime Control, State and Governance Boundaries

- Status: Accepted
- Date: 2026-07-13
- Scope: Native, LangGraph, Temporal and compatibility `WorkflowBackend`

## Context

Ananta has a stable Hub–worker architecture, a legacy `WorkflowBackend` port,
optional LangChain/LangGraph worker adapters and an optional Temporal client.
Without one explicit boundary, framework state could become a second task
system, workers could orchestrate other workers, or a fallback could silently
drop policy, audit or durability.

We need runtime choice without changing the ownership model, framework-neutral
contracts that work without optional dependencies, durable recovery, a
compatible migration for existing `WorkflowRequest` callers and a practical
exit from any optional framework.

## Decision

### 1. The Hub is the only control plane

The Hub owns plan compilation, runtime selection, policy, authorization,
approvals, budgets, task creation/delegation, side-effect decisions, canonical
events and operational read models. A worker executes one already-delegated
step. It does not create tasks, select a runtime, or invoke/orchestrate another
worker.

Temporal is durable infrastructure behind the Hub. Temporal Server owns its
technical workflow history and scheduling, not Ananta's task queue or policy.
Temporal Activities are executors of one Hub-authorized operation. An Activity
verifies the bounded authorization contract and submits the operation to the
authenticated internal Hub gateway. It never selects or calls an Ananta worker.

### 2. Two segregated runtime ports

`ExecutionRuntimePort` is the worker execution port. It validates an
`ExecutionPlan` and executes one signed delegated step. Streaming, checkpoint
and resume are separate optional interfaces so a simple implementation does not
pretend to support them.

For Native and LangGraph those optional capabilities terminate at the
Hub-owned runtime bridge and checkpoint gateway. The worker-side adapter
intentionally implements only validation and execution of one delegated node;
giving it checkpoint signing or resume-command authority would violate the
control-plane boundary. Capability truth therefore describes the complete
runtime family and names the owning port, while worker-profile truth describes
only the execution capabilities available inside that worker container.

`DurableRunInfrastructurePort` is the Hub-facing technical durability port. It
starts/describes durable runs and adapts Hub commands to signal/cancel/history.
It cannot create an Ananta task or authorize a tool. This separation protects
SRP and ISP: execution and durable orchestration are distinct responsibilities.
Both ports depend on runtime-neutral contracts, protecting DIP and keeping
optional SDKs out of Hub core contracts.

### 3. State and checkpoint authority

Authority is deliberately split by meaning:

- canonical business/task/approval/operation state is derived from Hub-owned
  canonical events and stores;
- runtime-private technical state remains private to Native, LangGraph or
  Temporal and is never accepted as user-visible success without projection;
- Hub checkpoints are signed, tenant/run/task/plan/policy/runtime/revision/fence
  bound and atomically persisted through the checkpoint port;
- Temporal History is authoritative for replay of a Temporal execution, while
  the Hub's versioned projection is authoritative for Ananta's canonical read
  model;
- a LangGraph checkpointer or Native in-memory object may not overwrite Hub task
  state. Container-local state is a cache, not recovery authority.

Checkpoint formats are not implicitly interchangeable. Resume validates the
originating runtime/version and all bindings. A mapping/version change rebuilds
and compares projections before promotion.

### 4. Canonical event history

Every runtime maps observations to versioned canonical events containing
tenant, workflow, run, step, attempt, correlation, causation, sequence and
dedupe identity. The event store is append-only with optimistic sequence and
transactional outbox semantics. Read models are rebuildable only from canonical
events. Unknown event versions are quarantined; stale/inconsistent history is
never current.

Temporal raw history stays in Temporal and is referenced rather than copied in
full. Runtime-private logs and UI state do not override canonical events.

### 5. Tool and provider access

Execution runtimes receive the least-privilege tools/artifacts/budgets signed
for one step. Every tool call traverses registry schema validation,
authorization, Hub policy, budget, approval, ownership fencing and the
side-effect ledger before invocation. Provider access traverses egress policy,
redaction, locality and deadline/retry/token/cost budgets. Model output and
retrieved content are untrusted data and cannot mutate the plan or authority.

LangChain may implement a provider, retriever or tool adapter. LangGraph may
implement worker-side graph execution. Neither is a task queue or policy owner.

Hub authorization envelopes use an asymmetric Ed25519 boundary in production.
Only the Hub mounts the private signing keyring; Native/LangGraph/Temporal
workers may mount the public verification keyring, which rejects private and
legacy symmetric fields. Native also revalidates authority online at the Hub.
The old shared-HMAC loader is disabled by default and exists only behind an
explicit development compatibility flag. A compromised Worker therefore has
no material with which it can forge Hub envelopes.

All workflow, tool-decision and LangGraph checkpoint internal routes require an
agent service credential/service JWT. Browser/user/admin JWTs are rejected even
when they carry an administrative role; interactive clients remain on the
public Hub API and cannot impersonate a runtime component.

### 6. Runtime selection and fallback

The Hub selects from versioned, evidenced capabilities using project, tenant,
profile and workflow policy. Inputs include `preferred_runtime`,
`allowed_runtimes`, required capabilities, data locality, budget, health and an
explicit fallback policy. The decision and rejected alternatives have stable
reason codes.

Fallback defaults to none. It is allowed only when explicitly enabled,
semantically equivalent and capability-preserving. Loss of authorization,
policy, audit, side-effect guard, durability or resume blocks fallback. Unknown
runtime/backend values and compiled-runtime failures fail closed rather than
switching to local/manual execution.

## Migration and compatibility

Migration is additive:

1. Existing `WorkflowRequest v1` remains readable through a compatibility
   adapter and legacy workflow routes remain stable.
2. `ExecutionPlan v1`, canonical events, signed state/checkpoints and segregated
   ports are introduced alongside the legacy backend.
3. Legacy `LocalWorkflowBackend` remains a simulated status/approval
   compatibility path; it is not advertised as delegated production execution.
4. Legacy Temporal calls adapt to the durable port and versioned history
   projection. New Temporal Activities return to the Hub gateway.
5. LangChain/LangGraph discovery remains authenticated/read-only until a real
   Hub-to-worker bridge is selected; direct Hub adapter execution is unavailable.
6. Profiles opt in per scope after contract-hash-matching conformance/security
   evidence. Existing runs stay pinned to their originating runtime/build.

API evolution uses new endpoints and optional fields. Old fields are not
renamed or removed without a versioned adapter. Runtime-private objects never
cross the API, and unsupported capabilities remain explicit.

## Exit strategy

An optional runtime can be removed without rewriting plans:

1. Put it in `drain`, stop new selection and retain query/cancel/reconciliation.
2. Complete or explicitly cancel/reconcile existing runs; do not translate
   private checkpoints or histories automatically.
3. Route new runs only to a runtime with equivalent evidenced capabilities.
4. Export canonical events, artifact/ledger references and release evidence;
   retain the old reader/build and verification keys for the replay/retention
   window.
5. Remove the concrete adapter and optional dependency after no profile allows
   it. Runtime-neutral contracts and Hub state remain.

If no compatible runtime exists, new runs remain blocked. The exit plan does
not weaken security or durability to preserve availability.

## Rejected alternatives

### Temporal as the Ananta control plane

Rejected because it would duplicate task ownership, policy, worker routing and
approval. It also couples Ananta business state to one infrastructure vendor.

### Worker-to-worker or LangGraph-managed worker orchestration

Rejected because workers would create an independent orchestration loop and
bypass Hub governance, task accounting and tenant isolation.

### Framework types in `ExecutionPlan`, control service or canonical events

Rejected because optional SDK upgrades would become Hub contract migrations and
Native-only installs would import unavailable packages.

### One broad runtime interface

Rejected because implementations would claim unsupported methods or hide
durability behind execution. Segregated optional ports make capability truth
testable and preserve LSP/ISP.

### Shared mutable database/state between Hub and workers

Rejected because it violates container boundaries, creates implicit trust and
makes fencing/audit ownership unclear. Communication uses explicit contracts,
tasks, artifacts and stores behind ports.

### Automatic local/manual fallback

Rejected because it can silently lose policy, authorization, audit, durability,
resume or compiled-graph semantics. Safe incompatibility is preferable to
incorrect success.

### Cross-runtime checkpoint translation

Rejected as a default because private state semantics differ. Only a separately
versioned, tested migration adapter with deterministic evidence may translate.

### Angular or clients calling Temporal/workers directly

Rejected because the client would bypass Hub authentication, policy, evidence
and tenant-safe read models.

## Consequences

Positive consequences are explicit ownership, optional dependencies, stable
contracts, deterministic selection, auditable side effects and reversible
runtime adoption. Costs are an additional Hub projection/gateway, duplicated
technical and canonical histories for Temporal, explicit capability gates and
the need to retain old runtime builds for replay.

An existing compatibility debt remains intentionally isolated: the operations
command gateway adapts the legacy `RunControlService` until all callers use the
new Hub control bridge. This is an SRP/DIP pressure point but does not authorize
a second control plane.

Worker code may temporarily import only these reviewed compatibility facades:

- `agent.providers.lc_lg` for provider configuration DTOs;
- `agent.services.workflow_runtime.components`;
- `agent.services.workflow_runtime.condition_evaluator`;
- `agent.services.workflow_runtime.execution_plan`;
- `agent.services.workflow_runtime.native_graph_contracts`;
- `agent.services.workflow_runtime.native_graph_ports`;
- `agent.services.workflow_runtime.parallel`;
- `agent.services.workflow_runtime.ports`;
- `agent.services.workflow_runtime.security`.

The latter modules contain framework-neutral contracts, validation or ports;
they must not acquire Flask, database, repository, approval, queue or concrete
Hub-service dependencies. This exact allowlist is namespace debt, not permission
to depend on the `agent.services.workflow_runtime` package generally. The
migration target is `ananta_contracts.workflow_runtime.*` (and a neutral
provider-config contract), followed by removal of every Worker-to-`agent`
allowance. The boundary scanner fails on new submodules or nested imports, so
the debt cannot grow implicitly.

## Enforcement and evidence

- Hub core contract tests reject LangChain/LangGraph/Temporal types/imports.
- Worker Temporal modules import neutral `ananta_contracts`, not Hub routes,
  databases, repositories, selection or policy services.
- All productive Worker runtime and adapter modules are scanned against the
  exact temporary compatibility-facade list above; broad package-prefix
  allowances are forbidden.
- Production Compose tests prove that Hub signing material is absent from every
  Worker/Temporal secret set and that internal gateways reject user JWTs.
- Route tests forbid concrete worker adapters in Hub route modules.
- Conformance/security gates validate capability truth, state/event contracts,
  replay, side effects and fail-closed fallback.
- The [runtime inventory](../architecture/workflow-runtime-inventory.md) records
  live, simulated, degraded and placeholder paths.
- The [threat model](../security/workflow-runtime-threat-model.md) and
  [rollout runbook](../operations/workflow-runtime-rollout.md) are normative for
  production enablement.
