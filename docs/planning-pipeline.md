# Planning Pipeline

## Current default (Learning phase)

Ananta runs planning in an LLM-first learning mode by default.
Deterministic templates remain available, but they are selected by policy and evidence, not blindly as global default.

Flow:
Goal -> planning_queued -> planning_running -> planned/failed

Runner profile semantics:
- `small`: compact English planning prompts and tighter output/context limits for weaker local models.
- `medium`: moderate limits with English prompt defaults.
- `off`: no runner-injected planning overrides; hub defaults remain active (safe baseline mode, not a hard planning disable).

## Safety boundary

LLM output may suggest task details, but policy-relevant decisions remain deterministic:
- no capability escalation from plan text
- no context scope escalation (for example forcing full/admin scope)
- no tool-permission activation from plan text

## Model-exhaustion recovery

The Hub may turn a terminal, bounded `model_recovery_signal.v1` into a
reviewable task plan when the effective model-routing policy enables
`segment_planning` or `propose_task_plan`.

Control flow:

Worker model attempts -> bounded exhaustion signal -> Hub policy validation ->
persisted recovery plan -> exact Hub approval decision -> Hub task queue

Operational rules:

- Workers report invocation facts only. They never create follow-up tasks or
  orchestrate another worker.
- Policy, security, client-error, and budget denials never enter recovery.
- Step-level recovery settings override the graph/global policy. An explicitly
  empty strategy list disables inherited recovery for that step.
- Recovery strategies have concrete, ordered Hub semantics:
  `stop` performs no compactor or planner call; `compact_context` creates one
  deterministic context summary and persists only its bounded metadata/hash;
  `segment_planning` and `propose_task_plan` create at most one draft through
  the planning saga. The full chain compacts once and then proposes one
  segmented draft.
- `segment_planning` and `propose_task_plan` both require the explicit
  `require_approval` strategy and
  `require_approval_for_generated_plan=true`. No generated child reaches the
  task queue before the exact plan digest is granted.
- A verified terminal model chain is never automatically forwarded to a
  second Phi/Gemma chain by the outer Autopilot loop. Disabled, stopped,
  invalid, denied, or failed recovery enters review (or fails when review is
  disabled) with zero cooldown.
- Recovery plans have depth one and cannot recursively generate another
  recovery plan.
- The Hub stores the plan as `pending_approval` and binds approval to its exact
  digest, goal, source task, recovery key, and approval request.
- Recovery materialization is manual by default. A productive unattended run
  may explicitly enable
  `approval_lifecycle.auto_approval_policy.<governance_mode>.recovery_plan_materialization`.
  The Hub then records a digest-bound `auto_policy` grant and reconciles it
  through the same durable approval outbox. The policy applies only to the
  closed `planning.recovery_plan.materialize` request scope; incomplete scopes
  and tools listed in `human_required_tools` remain blocked.
- Operators inspect a specific plan with
  `GET /goals/<goal_id>/plans/<plan_id>` and may edit an unmaterialized node
  with the admin-only
  `PATCH /goals/<goal_id>/plans/<plan_id>/nodes/<node_id>`.
- Editing a plan invalidates the old digest. A stale grant is consumed and a
  fresh approval request is created; digest validation and node editing share
  the plan lock, so an edit cannot race past the grant check.
- Only an admin may decide the recovery materialization approval through the
  approval API. Generic Run-Control approval commands cannot decide this
  recovery-specific tool.
- The policy hash is re-evaluated when a grant is applied. Removing or changing
  the effective recovery policy consumes the obsolete grant without creating
  work.
- Before materialization, the Hub revalidates the proposal, dependency graph,
  quality gates, goal state, source-task state, and all approval bindings.
- Child task IDs are deterministic per persisted plan/node. Recovery children
  reference the source through `source_task_id`, never `parent_task_id`, so the
  source-to-child wait relationship cannot form a dependency cycle.
- Newly materialized recovery children remain `paused` until the exact
  approval grant has been consumed. Afterwards only DAG roots move to `todo`;
  dependent children and the source remain `blocked_by_dependency` until
  dependency reconciliation proves them ready. A terminal source or goal
  cancels the children instead.
- Materialization binds the source's complete dependency list: pre-existing
  dependencies, the exact ordered set of approved children, their combined
  authoritative list, and a deterministic digest. New dependencies cannot be
  added or removed after approval; the finalizer fails closed on any mismatch.
- Workers receive only an assignment-bound Recovery manifest from the Hub.
  They use isolated databases and cannot publish Task status, proposal,
  verification, or repository writes directly into the Hub control plane.
- Suppressed Worker diagnostics cross the boundary in the closed
  `ananta.recovery_worker_result.v1` envelope. Its task, phase, payload, size,
  and digest are checked under the matching dispatch lease. The envelope is
  Worker evidence only; Hub verification records and artifact provenance are
  always derived independently by the Hub.
- Recovery artifact references cross split databases only through the strict
  registered-Worker ingress. Before returning a terminal result, the Worker
  publishes a closed, assignment- and dispatch-lease-bound metadata manifest.
  The Hub reads only the identical relative path from the explicitly mounted
  task workspace, rejects traversal, symlinks, size/hash drift and stale
  leases, then creates deterministic Hub-owned Artifact/Version records.
  Worker database IDs remain untrusted provenance; exact duplicate ingress is
  idempotent and safely repairs a partially persisted Hub record.
- An execute result is accepted only once and is bound to phase, terminal
  status, and an accepted-result digest. Timeout/cancellation revokes the
  capability under the same owner locks; only a terminal result with a valid
  `result_accepted` lease proof wins that race. The Hub stages verified output
  non-terminally and publishes terminal status plus lease acceptance in one
  Task-aggregate commit. Terminal rows without that proof fail closed instead
  of leaving the source blocked indefinitely.
- Source completion aggregates the full approved PlanNode set and only
  Hub-verified child results. Its post-commit work uses a durable marker and a
  stable idempotency key. Each delivery claim has a unique attempt ID, and
  acknowledgement or failure may update only that exact claim. A crash after
  the source commit can therefore be replayed without reopening the result
  decision, while a stale attempt cannot overwrite a newer successful replay.
- Generic task administration cannot independently cancel, retry, archive, or
  delete an active recovery child. These requests fail with HTTP 409 and the
  authoritative source/plan binding. A terminal child may be manually cleaned
  up only after both its source and Goal are terminal and no Worker dispatch
  lease remains in flight.
- Archived recovery children and sources cannot be generically restored.
  Re-execution requires a new Hub-owned plan; retention jobs preserve archived
  recovery lineage in both database and JSON fallback stores.
- PostgreSQL deployments serialize proposal creation for a recovery key with
  an advisory lock across Hub processes. Exact plan edits and materialization
  use a separate per-plan PostgreSQL advisory lock; non-PostgreSQL development
  runtimes retain process-local locks.
- Granted recovery approvals act as durable outbox markers. Each Hub tick
  reconciles interrupted `granted` actions and incomplete `consumed` DAG
  releases idempotently, so a process interruption cannot leave approved work
  permanently paused.

## Provider-budget rolling upgrades

Run-wide, signed-node, and provider-profile reservations commit atomically.
Before deploying a release that introduces the signed-node budget row, drain
in-flight provider calls created by the previous release. A legacy run-only
reservation is not backfilled because its historical node attribution cannot
be proven safely. Replay or reconciliation of such a reservation fails closed
with `provider_scoped_budget_migration_required`; the operator must drain or
terminate that old run before retrying it under the new release.

## Planning track contract

Planning output is contract-first and grounded in `todos/todo.track.schema.json`.

### Organization planning: Category first

Organization-bound Goals add a mandatory earlier planning artifact. Research
first produces a Category-Todo conforming to `todos/todo.schema.json`; it is a
versioned portfolio/research plan and never creates Worker Tasks. The Hub
validates schema and item dependencies, recomputes Category summaries, checks
claim/evidence references against the exact assignment allowlists and requests
a revision/digest-bound promotion decision. Callers and Workers never infer or
mint source identifiers. The Hub automatically issues them from admitted,
immutable source and run bindings; missing or unknown `SRC_*`/`RUN_*` values
remain unverified.

Organization Goal intake is an explicit passive Hub transition before that
research phase. `POST /api/organizations/<organization_id>/goals` creates only
one idempotent root Goal with server-owned tenant, project, Organization and
principal bindings. It does not invoke the legacy planner, create Tasks or
write to the dispatch queue. A separate Category-research request binds the
Goal to an authoritative source catalog and an Organization role slot.

The Organization Source Catalog is itself published by the Hub from bounded
queries against one active, admitted Knowledge Index. Callers supply only the
connection, query intent and result limit; the Hub revalidates the exact source
revision, admission receipt, index run and on-disk manifest before assigning
deterministic `SRC_*` identities. The persisted Catalog and its publication
binding are content-free. At research-Task creation, the Hub locks that exact
Catalog Task, revalidates the still-active lineage, hydrates only the immutable
record selectors and verifies each content digest. Content exists only in the
task-bound `ContextBundle`; stale lineage, tampering or readiness failure rolls
back the Task, retrieval run and bundle together.

Category-research readiness and Task creation resolve one concrete active
Organization Role Assignment and registered Agent with `planning`, `research`
and `source_analysis` capability. That binding is persisted by the Hub and is
revalidated together with lifecycle, topology, capacity and the WorkerJob
lease immediately before forwarding; Organization Tasks never fall back to a
global Worker. The destination uses a Worker-only intake and accepts only the
exact payload and ContextBundle protected by a short-lived capability signed
with the Hub-private Ed25519 key. The Worker service token authenticates only
the transport; the Worker receives a public verification keyring and cannot
mint Hub authority. The Worker executes the delegated Task and cannot reroute
it.

Only the promoted Category revision can be transformed into one or more
Planning Tracks. The normal second phase creates exactly one deterministic,
role-bound `planning_track_task` for that immutable revision and the complete
canonical set of its non-deferred `source_category_item_ids`. The Track Planner
is an execution-only Worker: it returns candidates through the closed
`organization_track_planning_result.v1` callback and cannot create or dispatch
follow-up work. Its capability is bound to the exact Task, assignment, current
WorkerJob/dispatch lease, expiry, and canonical payload digest.

The Hub re-reads that binding inside the Track write transaction, validates
each candidate against `todos/todo.track.schema.json`, recomputes summaries and
quality gates, enforces the Category item scope and Worker authority ceiling,
and validates the complete cross-Track DAG. Track lineage includes the source
Category artifact, revision/digest, result Task/assignment/lease and exact item
mappings. A successful callback persists passive Track revisions only; it does
not adopt a Track, materialize a runtime Task, or write to the dispatch queue.
Transport retries collapse onto one deterministic result idempotency key, and
a different result digest fails closed.

The direct Hub-admin derivation endpoint and fixed reference-workflow adapter
remain additive compatibility paths, but both use the same Track validation
and persistence service. Promotion, Track adoption and Task materialization use
separate one-shot grants and separate transaction boundaries; every step
revalidates current digests and policy before writes. Organization-less legacy
Goals keep their compatibility adapter.

The productive Organization path is:

`Category research Task -> validated Category revision -> promotion ->`
`bound planning_track_task -> capability-bound candidate callback ->`
`Hub-validated Track persistence -> adoption -> guarded materialization ->`
`Hub routing -> durable dispatch outbox -> Worker assignment`

Operational invariants:

- Category and Track artifacts are passive until their distinct Hub
  transitions succeed. Research result ingress cannot write Track Tasks.
- Track-planning result ingress accepts exactly the bound Category revision and
  item scope. Worker-provided organization, team, role-slot, Agent, Worker,
  tools, capabilities, context-right expansion, or budgets are rejected rather
  than treated as authority.
- The final Organization, unit, team, role-slot, role-assignment, and Agent
  binding is resolved from authoritative Hub rows immediately before the Task
  compare-and-swap. Target fields supplied by a planner, Worker, or caller are
  advisory only.
- `execute-next` requires an adopted Track, stable mapping, committed
  materialization receipt, completed dependencies, current policy hash, and an
  eligible active role assignment. There is no legacy or implicit
  materialization fallback for Organization work.
- Task-CAS and the first dispatch intent commit together. Network delivery
  occurs only afterwards through the Hub-owned outbox port. Delivery has a
  bounded processing lease, deterministic logical Worker task ID, exponential
  retry, terminal retry state, and crash recovery from the authoritative Task
  and WorkerJob receipt.
- The generic Task delegation service accepts a Planning-selected Agent only
  after re-reading the exact outbox, mapping, adopted Track, active role
  assignment, Task state, and unexpired processing lease. JSON context alone
  is never dispatch authority.
- Fixed reference workflows produce ordinary Track candidates and therefore
  use the same derivation, adoption, materialization, routing, and dispatch
  services; they do not create a parallel workflow executor.

### Hub evidence identity issuance

Evidence identity is a Hub control-plane responsibility and never a planning
model or Worker responsibility. The general registry persists two immutable
records:

- `SRC_*` admission binds tenant/project, origin digest, content digest,
  admission-policy digest, evidence scope, synthetic classification and
  issuer. Hub-issued identities are content/binding-addressed; externally
  issued compatibility IDs require a named issuer and the same immutable
  registration.
- `RUN_*` reservation occurs before execution and binds the Task, assignment,
  dispatch lease, repository revision, complete `SRC_*` input set, input,
  execution-profile and environment digests. The Worker receives only a
  closed, digest-protected assignment projection.

Result ingress may move a reservation to a terminal state only under the exact
assignment and dispatch lease. Release verification additionally requires a
successful result, exact source/task/revision bindings and a compatible
evidence scope. Test or synthetic identities can drive fully automatic policy
tests but can never satisfy production release gates. A local command that was
not reserved before execution is deliberately not upgraded to `RUN_*`
authority after the fact.

The Organization Source Catalog publisher and Category research run service
remain compatible specialized adapters. They already issue deterministic
catalog-local references through the Hub and must converge on the general
registry incrementally; no Worker-facing compatibility path may become an
independent issuer.

Workers cannot promote, adopt or materialize. An assigned Worker may submit a
closed follow-up proposal through its assignment/lease-bound callback. The Hub
validates and classifies it as a Track amendment, chooses the destination and
approval path, then materializes at most one idempotent Task. Generic follow-up
and AutoPlanner paths reject organization-bound Worker credentials.

Required core fields:
- `version`
- `owner`
- `track`
- `status_scale`
- `priority_scale`
- `risk_scale`
- `milestones`
- `tasks`
- `tasks_status_summary`

Optional extensions (allowed by design) include:
- `critical_path_tasks`
- `tasks_type_summary`
- `progress_summary`
- `execution_stage_summary`
- `summary_notes`
- `end_summaries`

Track-planning profile (`track_planner`) requirements:
- minimum task quality fields: `title`, `risk`, `acceptance_criteria`
- optional task fields: `depends_on`, `type`, `milestone_id`
- summary policy: `tasks[]` is single source of truth; all summary blocks are derived caches
- summary engine recomputes `tasks_status_summary`, `tasks_type_summary`, `progress_summary`, `weighted_progress_summary`, milestone progress and `derived_summary_metadata`
- task `progress_percent` semantics are normalized by status (`todo=0`, `done=100`, `in_progress|partial=1..99`, `blocked=0..100`)
- prompt template: `prompts/planning/track_planning.j2`
- organization Category-to-Track phase prompt:
  `prompts/planning/organization_track_planning.j2`

Planning track persistence/validation:
- planner context envelope filters `available_artifacts` by `allowed_source_refs` and records denied refs
- planning track output is persisted as `artifact_type=planning_track` with execution provenance
- schema validation returns structured issues (`path`, `reason_code`, `human_message`)
- summary consistency is deterministically recomputed; repair mode can auto-fix summary mismatch
- summary recalculation state is persisted as `summary_recalculation_status` (`not_needed`, `recalculated`, `repaired`, `failed`) with `old_summary_hash` and `new_summary_hash`
- persistence overwrites derived summary blocks with the recomputed canonical values before write
- JSON repair pipeline is capped to one repair attempt; failed repair remains degraded/failed, never active plan
- quality gates validate large-goal minimum task count, critical path references, and milestone task references; warnings are surfaced in TUI
- operator TUI commands: `:plan track`, `:plan track --from-goal <goal-id>`, `:plan track adopt <output-id>`, `:plan track reject <output-id>`, `:plan track execute-next`, `:plan track sync-status <plan-task-id> <status>`, `:plan track diff <left> <right>`
- adopted planning tracks materialize stable internal tasks (`plan_task_id -> internal task id`) and persist mapping/source/context refs in output extensions

Planning-track documentation:
- Contract and execution flow: `docs/architecture/planning-track-contract.md`
- Planner prompt and worker role: `docs/development/planner-role-prompt-worker.md`

## Transition to deterministic-first

Deterministic-first is a later policy mode (`deterministic_first`) once metrics and review evidence are sufficient.
This transition is evidence-based and can be done per mode/model/profile.

## Related TODOs

- `todo.llm-first-planning-learning-and-response-behavior.json`
- `todo.planning-mechanism-hardening.json`
