# Enterprise agentic Scrum organizations

Ananta models a multi-team organization as a declarative extension of the
existing hub–worker architecture. The Hub remains the only control plane: it
owns planning artifacts, the task queue, routing, approvals, dependencies,
gates and lifecycle transitions. Teams, roles and workers contribute execution
results and bounded proposals; they never address or orchestrate another
worker.

## Public model

The public hierarchy is:

1. **Organization Blueprint** — portable, versioned definition.
2. **Organization Instance** — scope-bound materialization of an exact
   definition revision.
3. **Coordination Unit / Value Stream** — responsibility and routing hierarchy.
4. **Team Instance** — organization link to an existing Team Blueprint.
5. **Role Slot** — required cardinality, capabilities and assignment policy.
6. **Agent Assignment** — bounded principal assigned to one role slot.

Official Scrum accountabilities stay limited to Product Owner, Scrum Master
and Developers. Backend engineering, UX, quality, research, SRE, security or
architecture are specializations or organization roles. A specialization
never silently gains a stronger Scrum accountability or permission set.

The hierarchy is derived only from `parent_unit_id`. Cross-team relations are
stored in a separate typed graph. Its namespaces have different semantics:

- `hierarchy`: immutable `contains` projection;
- `organization`: declared dependency, handoff, gate and escalation relations;
- `runtime`: read-only task dependencies and current execution state;
- `presentation`: per-principal layout preferences, never domain state.

## Standard organizations

The production catalog provides a data-driven composition band of five to ten
teams. Eight is the default and the sole full acceptance reference. The same
compiler expands every size; there are no service or UI branches for a
particular team count.

This Enterprise family is intentionally comprehensive. Its eight-team
reference contains 82 role slots and a default assignment capacity of 73. A
role slot is a responsibility and assignment position, not an implicit demand
for one distinct human or Agent. One compatible principal may hold several
explicitly permitted slots; capacity and separation of duties still apply.

| Count | Composition change |
| --- | --- |
| 5 | Portfolio coordination, two product-delivery teams, research/discovery and platform/SRE |
| 6 | Add quality/security/release |
| 7 | Add architecture governance |
| 8 | Add proof of concept (default reference) |
| 9–10 | Scale out the product-delivery group |

The seven reusable Team Blueprints are:

- Enterprise Product Delivery Scrum
- Portfolio Product Coordination
- Research and Discovery
- Proof of Concept
- Platform DevOps SRE
- Architecture Governance
- Quality Security Release

The medium reference expands to exactly eight team instances: two delivery
teams and one instance of every other type. It contains one coordination unit,
three value streams, twelve hierarchy `contains` edges and eight active
organization relations.

## Lean company profiles

The additive `lean_company_organization@1` production family is intended for
small companies that do not need the full Enterprise role catalog. It uses the
same deterministic compiler, Hub policies, grants and atomic instantiation
path. It does not weaken or replace the five-to-ten-team Enterprise family.

| Roles | Teams | Composition |
| ---: | ---: | --- |
| 5 | 2 | Direction plus one four-role Delivery Cell |
| 8 | 3 | Add three-role Discovery |
| 12 | 4 | Add four-role Enablement |
| 16 | 5 | Add a second Delivery Cell |
| 20 | 6 | Add a third Delivery Cell |

All Lean role slots have a default cardinality of one, so the displayed role
slot count and default assignment capacity are identical. The number of
distinct assigned Agents may still be lower where an assignment is compatible,
within capacity and allowed by separation-of-duties policy. The setup UI
therefore reports all three concepts separately:

- **Teams** are materialized Team Blueprint instances.
- **Role slots** are explicit responsibility and assignment positions.
- **Default assignments** are the planned sum of slot cardinalities, not a
  count of unique principals.

Direction owns the company Product Goal and decision boundaries. Each Delivery
Cell owns a bounded product slice. Discovery produces grounded research and an
independent review. Enablement covers platform, reliability, security and
release evidence. These roles exchange artifacts and proposals only through
the Hub; a compact organization never enables Worker-to-Worker orchestration.

The Lean workflows make the activation chain concrete. “Reacts to” means that
the Hub has received and accepted the declared predecessor/input; it never means
that one role invokes another role directly.

| Role | Hub-routed task | Declared trigger/input | Output or gate |
| --- | --- | --- | --- |
| Portfolio Product Owner | prioritize company goal | Organization Goal intake plus allowed evidence | company goal, priority decision and accepted requirements |
| Delivery Product Lead | plan increment | accepted company goal/requirements from a versioned Hub handoff | delivery plan and optional enablement need |
| Delivery Technical Lead | define solution boundary | completed delivery plan | solution boundary |
| Product Engineer | implement vertical slice | delivery plan and solution boundary | product increment and test evidence |
| Quality Engineer | independently verify increment | completed implementation and test evidence | quality report; independent Product Lead approval gate |
| Research Lead | frame delegated research | Hub-selected research question and company goal | research brief |
| Requirements Analyst | analyze requirements | completed research brief | requirements brief |
| Evidence Reviewer | independently review evidence | completed requirements analysis | evidence review and accepted requirements; independent Research Lead approval gate |
| Platform Lead | plan enablement | delivery need and company goal accepted by the Hub | platform plan |
| Platform Engineer | build enablement | completed platform plan | platform capability and deployment evidence |
| Reliability Engineer | verify readiness | platform capability and deployment evidence | operational readiness; independent Security approval gate |
| Security Engineer | verify release controls | completed readiness verification | security evidence and readiness handoff; independent Reliability approval gate |

Only rows whose Team Blueprint is present in the selected 2--6-team profile are
materialized. A missing optional Discovery or Enablement team therefore leaves
no hidden Worker or direct-call fallback; the Hub uses only the inputs and
routes admitted by the active profile.

Four reduced Enterprise two-/three-team compositions exist only in the
injected test catalog. They are useful for cheap contract and fake-runtime
verification, but are not production presets and do not appear in the setup
UI. The productive Lean two-/three-team definitions above are distinct,
complete presets. A reduced Enterprise custom composition requires a fresh,
one-shot admission exception bound to principal, tenant/project scope,
composition digest, policy revision and expiry. Missing platform, quality or
governance capabilities remain visible as dry-run gaps.

Custom N is supported up to the effective deployment limit. Counts outside
the standard band require an explicit custom composition and a valid admission
decision. All units, teams, slots, assignments, relations, workflows, patches,
pages, depths and bundles are checked against the same revisioned limit
profile before planning and again immediately before a write.

The production Custom-N flow is explicit and Hub-owned:

1. A principal with project `MANAGE` posts exact Team Blueprint counts, a
   reason, TTL and an idempotency key to
   `POST /api/organization-blueprints/<key>/admission-exceptions`.
2. The Hub validates the current definition and limit profile and issues a
   grant bound to tenant, project, principal, definition key/version/revision,
   policy hash and a canonical digest of the normalized counts. TTL is bounded
   to 60–3600 seconds (900 by default).
3. Compile receives the opaque exception reference plus those exact counts.
   It validates the still-issued grant read-only and binds the reference and
   composition into the signed compile token; repeated dry-runs do not consume
   the exception.
4. Instantiate recompiles from the current catalog, then atomically changes
   the exception from `issued` to `consumed`, consumes the separate plan-bound
   precreation admin grant and writes the organization aggregate.

Any scope/principal mismatch, changed count, stale definition or policy,
expiry, revocation or reuse fails closed. A failed materialization rolls back
the exception consumption with every aggregate write. A successful exception
cannot authorize another organization; only replaying the identical successful
Instantiate idempotency key returns the already-created result.

## Planning and execution

Organization planning is deliberately two-stage:

```text
Goal
  -> research-grounded Category-Todo (todos/todo.schema.json)
  -> exact promotion approval
  -> one or more Planning Tracks (todos/todo.track.schema.json)
  -> exact adoption/materialization approval
  -> Hub-owned Tasks
  -> delegated Worker execution
```

The Category-Todo is a versioned research/portfolio artifact. It does not
create tasks. The Hub validates schema, recomputes summaries, verifies every
claim reference against the assignment allowlist and binds promotion to the
exact revision and digest. No source or run identifier is inferred: an
unknown, missing or merely text-mentioned `SRC_*`/`RUN_*` reference is
unverified and blocks promotion.

Only a promoted Category revision can be partitioned into Planning Tracks.
Each Track records the exact source Category revision/digest and mappings for
its items. Cross-Track dependencies must preserve the Category DAG. The Hub
again validates and recomputes derived summaries before adoption. Promotion,
Track adoption and task materialization are separate one-shot decisions.

An Organization Goal owns the shared Product Goal and authoritative task DAG.
Team Goals and backlogs are projections with explicit bindings to unit, team
and role-slot candidates. Completion rolls up from Tasks to Tracks, Category
and Organization Goal; a child or legacy single-goal finalizer cannot complete
the organization prematurely.

## Worker task proposals

A worker may discover necessary follow-up work while executing its exact
assignment. It can return one closed, assignment-bound proposal through its
restricted callback capability. The proposal contains bounded work content,
reasoning, dependencies, risk and optional role/team hints. Hints are not
commands and an agent target cannot be forced.

The Hub verifies assignment, lease, scope, proposal depth, policy, budget and
evidence; classifies the proposal as a Track amendment; selects the eligible
team/role/agent; and requests approval when required. Only then may it amend
the plan and materialize a task. Direct Worker writes to the Hub task database,
queue, generic follow-up endpoints or AutoPlanner are forbidden. Replay is
idempotent and cannot create a recursive proposal loop.

## Routing, handoffs and governance

Routing is a pure Hub decision over team scope, required capabilities, active
role slots, effective rights, backend/runtime availability, risk, capacity,
budget and separation of duties. Every excluded candidate and the effective
policy hash remain auditable. The effective permission set is the intersection
of governance, organization, team, slot and task overlays; prompts can only
reduce behavior and never grant a capability.

Cross-team work uses Hub-owned dependencies and a versioned handoff contract.
A handoff binds producer/consumer, artifact type/version/digest, acceptance
criteria, evidence and current state. `accepted`, `rejected` and
`needs_changes` transitions are compare-and-swap and idempotent. The receiver
never invokes the producer directly.

Separation of duties prevents a principal from producing and approving the
same code, security, compliance or release decision, including indirect
multi-team assignments and retries. Small test organizations may combine only
explicitly low-risk duties under a bounded human gate.

Budgets are hierarchical (organization, unit, team, workflow and task) and
reserve tokens, cost, wall time and parallel work atomically before dispatch.
Feedback/rework loops are bounded by attempts, elapsed time and cost and end
in success, block or Hub escalation.

The production Hub persists these controls in dedicated scoped ledgers:
`organization_budget_usage`, `organization_budget_reservations`,
`organization_runtime_events`, `organization_team_handoffs` and
`organization_workflow_loop_states`. Reservation and settlement run in one
transaction with an Organization lock; handoffs and workflow loops use scoped
revision compare-and-swap. In-memory adapters remain test/development seams.
The dispatch integration is the `OrganizationDispatchBudgetPort`: only the
Hub supplies already-resolved authoritative limits, and no HTTP request can
raise a budget or provider policy.

`GET /api/organizations/{organization_id}/runtime` returns the revision- and
snapshot-bound event replay plus authoritative task/dependency status,
budget usage, handoffs and loop state. Handoff submit/decision endpoints are
Hub-mediated, project-scoped and idempotent. The artifact adapter composes the
existing Goal Artifact graph (goal membership, provenance and Hub
verification) with the existing versioned artifact tables (immutable
SHA-256); neither store is duplicated. Its evidence adapter requires exact
assignment/dispatch-lease bindings, exact provided `SRC_####`/`RUN_####`
allowlist membership and explicit context-scope release. The submitted
versioned handoff definition must also match an active Organization relation.
Runtime records contain only references and redacted payloads, never prompts,
credentials or artifact bodies.

## When a role becomes active

An active role slot means only that the structural position exists. It does
not mean that an Agent is currently working. The read-only role-activation
projection keeps these five levels separate:

1. **Slot active** — the role position belongs to the current Organization
   revision and its Team is eligible for work.
2. **Assignment recorded** — active assignment rows cover none, the minimum or
   the desired cardinality of the bound slots. This count does not prove Agent
   registration, capabilities, free capacity, separation-of-duties eligibility
   or Worker liveness.
3. **Local Task readiness** — an exactly bound Task is in a pre-routing state
   and every persisted `depends_on` Task is complete in the same scoped read.
   Declared external inputs and handoffs remain a separate Hub decision and
   are not implied by this local fact.
4. **Hub routed** — that Task carries a valid Hub planning-dispatch record and
   a persisted assignment while its Task status is routed.
5. **Worker executing** — the Task's current WorkerJob is running, has started,
   matches the assigned Worker and is protected by the matching, active,
   unexpired lease.

Levels three through five are projected only when the Task binding matches the
current Organization revision, workflow reference and content hash, step,
Team, role slot, gate, handoff, failure policy and verification specification.
Tasks are queried with exact tenant, project and Organization scope; jobs and
leases are accepted only through those Task IDs. Missing, stale, conflicting
or out-of-scope evidence remains `unknown`. An exact negative observation is
reported separately as `observed_false`; it is never inferred from a role slot
or assignment row. Worker addresses and job or lease identifiers are not
returned.

A role never reacts directly to another role. An upstream role produces a
versioned output; the Hub evaluates the declared workflow dependency, required
inputs, gate and current routing policy and may then assign the downstream
Task. The `role-activation-map` view shows this desired workflow separately
from assignment coverage and the three runtime facts. Unbound steps remain
`unknown`, never inferred from a structurally active slot.

When a workflow can target several identical Delivery Cells, the Lean
Delivery definition requires an explicit `target_unit_id` in reference-
workflow preview/derive requests. The Hub validates that unit against the
workflow selector and binds every generated step to that exact Team. An
ambiguous request fails closed instead of silently selecting the first Team.

Worker Task Proposals form a separate advisory lane. They show the proposer and
target hints, followed by Hub classification, approval, Track amendment,
materialization and final routing. A hint is never rendered as an assignment.

## Management UI

`/organizations` is a dedicated Angular feature. The setup wizard first selects
the Organization family. Enterprise exposes its server-provided five-to-ten
team band and recommends eight teams; Lean Company exposes 5, 8, 12, 16 or 20
role slots. Compile is always dry-run first and shows topology counts, role
slots, default assignment capacity, capability gaps, effective limits, planned
writes, warnings, blockers and the exact plan digest.

Hierarchy, 2D graph and 3D graph are synchronized projections of the same
definition revision and runtime overlay. Selection, focus and filters survive
switching views. Hierarchy supports keyboard navigation and lazy children. The
graphs use the Visual Process interaction vocabulary for pan, zoom, fit, focus
and layout-only movement. Text labels and status attributes remain available
without color. Runtime edges are read-only.

The 3D graph renders every loaded role slot and assignment. Operators may set
an explicit leadership scope and presentation importance for stable role keys,
choose node-size and node-color metrics and configure edge colors and widths.
No leadership level is guessed from words such as “Chief”, “Lead” or
“Manager”. The settings are presentation-only and do not change rights,
capacity, Task priority or Hub routing. A visible legend explains the active
color metric, small/medium/large node sizes, edge kinds and minimum/median/
maximum edge strength. A synchronized DOM list and automatic WebGL fallback
preserve the same information for keyboard and assistive-technology users.
When reduced motion is requested, the moving 3D simulation is deliberately
replaced by the static synchronized list, hierarchy or 2D projection.
The interactive settings panel edits concrete stable role-slot keys. The
validated profile contract also supports template-wide defaults, which are
currently supplied through a profile rather than edited in that panel.

Typed add/remove/reparent/connect/assign edits first create a draft plan.
Apply requires the exact definition revision, patch digest, limit-profile
revision/hash, idempotency key and organization-admin grant. Removing active
work requires explicit drain, migrate or archive semantics. Drain is a
Hub-owned transactional quiesce (tasks are paused and leases/jobs are
released). Remove+migrate additionally requires a fully scoped successor
Organization/Unit/Team/Role-Slot binding and creates lineage-linked successor
tasks; a target is never inferred.

## Lifecycle and portability

Organization instances transition through `draft`, `validated`, `active`,
`paused`, `completed` and `archived`. Active work, leases, gates and handoffs
block unsafe deletion. Archive preserves definition snapshots, goals, tasks,
assignments, relations, artifacts and audit lineage. Recovery creates no
automatic rerun and needs a newly validated activation.

Organization Bundle v2 transports the complete versioned definition graph,
not a running Organization Instance. Export omits source tenant, project and
organization identifiers, local database IDs, compiled plans, assignments,
credentials and agent URLs. `organization_instances` and `assignments` remain
readable only as legacy v2 compatibility fields; a non-empty value blocks
preview and apply.

Import is preview-first, bounded before expensive parsing and atomic on apply.
A write accepts only the unchanged, scope-bound preview plan. To move an
organization, import its definitions first, then explicitly compile the root
Organization Blueprint inside the authenticated target project and instantiate
that server-recompiled plan. The target Hub allocates all IDs and operators
bind target-local agents only after instantiation. A source-scoped compiled
plan is never a portability artifact. Legacy Team Bundle v1 endpoints remain
compatible.

## Eight-team demo flow

The canonical demo uses only the medium reference:

1. Compile the eight-team blueprint and inspect counts/gaps/writes.
2. Instantiate the exact plan without starting Workers.
3. Create an Organization Goal and produce a research Category-Todo.
4. Promote the exact grounded revision and derive Planning Tracks.
5. Adopt Tracks and let the Hub route Tasks through Portfolio, Research, PoC,
   Architecture, two parallel Delivery teams, Quality/Security/Release and
   Platform/Deployment.
6. Triage one valid Worker proposal as a Track amendment and reject one policy
   escalation.
7. Show a rejected handoff or bounded rework loop, then the verified release
   path.
8. Switch hierarchy/graph without losing selection, preview and discard one
   topology draft, and export a definition-only Bundle v2.

Operational commands and recovery procedures are documented in
`docs/operator/enterprise-organizations.md`.
