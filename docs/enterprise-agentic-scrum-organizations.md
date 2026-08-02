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

Four two-/three-team compositions exist only in the injected test catalog.
They are useful for cheap contract and fake-runtime verification, but are not
production presets and do not appear in the setup UI. Each requires a fresh,
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

## Management UI

`/organizations` is a dedicated Angular feature. The setup wizard starts at
eight teams and exposes server-provided standard sizes from five through ten.
Compile is always dry-run first and shows topology counts, role slots,
capability gaps, effective limits, planned writes, warnings, blockers and the
exact plan digest.

Hierarchy and graph are synchronized projections of the same definition
revision and runtime overlay. Selection, focus and filters survive switching
views. Hierarchy supports keyboard navigation and lazy children. The graph
uses the Visual Process interaction vocabulary for pan, zoom, fit, focus and
layout-only drag. Text labels and status attributes remain available without
color. Runtime edges are read-only.

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
