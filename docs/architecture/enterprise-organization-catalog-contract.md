# Enterprise organization catalog contract

## Decision

Enterprise organization definitions extend the existing Hub-owned task system.
They do not introduce a second orchestrator, a worker-to-worker protocol, or a
second workflow runtime. The portable source of truth is the strict Draft
2020-12 contract in
`schemas/blueprints/organization_blueprint_catalog.v1.json`; persisted rows and
UI projections are adapters over a validated, versioned definition.

All portable references use a stable `key@version`. Database identifiers,
container addresses, queue priorities, credentials and environment-specific
agent endpoints do not belong in the catalog.

The Bundle-v2 portability boundary ends at the definition graph. A runtime
Organization Instance, its compiled plan and its assignments are bound to the
source tenant/project and are not portable definitions. Export therefore emits
empty legacy `organization_instances`/`assignments` sections and no source
scope metadata. Import rejects non-empty legacy sections. After definition
import, the target Hub must recompile the selected `key@version` in its own
authenticated scope, allocate target IDs and accept target-local assignments
through the normal compile/instantiate APIs.

## Catalog assembly

The checked-in catalog is deliberately split into independently reviewable
fragments:

- `templates.json` and `templates.d/*.json` contain role templates and shared
  prompt appendixes;
- `blueprints.d/*.json` contain reusable Team Blueprints;
- `organizations.d/*.json` contain Organization Blueprints, handoff
  definitions, the single full acceptance fixture and bounded fixture
  metadata;
- `workflows.d/*.json` contain deterministic workflow definitions;
- `policies.d/*.json` contain policy contracts and revisioned policy content.

A production catalog loader must assemble them in this order:

1. parse every JSON document and validate its fragment schema;
2. select versioned role entries and reject duplicate `key@version` values;
3. select versioned Team Blueprints and map their rich
   `artifact_contracts` field to aggregate `team_blueprints[].artifacts`;
   the legacy seed `artifacts` task list remains only a compatibility
   projection;
4. load workflows, policies, handoffs, limit profiles and organization
   definitions, rejecting missing or ambiguous references;
5. represent policy content in the aggregate by a contract reference and a
   canonical SHA-256 digest;
6. validate the assembled document against
   `organization_blueprint_catalog.v1`; and
7. run semantic checks for reference closure, parent-kind compatibility,
   acyclic workflows, role-slot cardinality, limit profiles and separation of
   duties before exposing any production definition.

Validation is fail-closed. A partially assembled catalog is not usable. The
loader may adapt definitions to persistence models, but must not silently
default missing security fields or rewrite versioned references.

At Hub seed startup this order is explicit: legacy/template fragments are
reconciled first, reusable Team Blueprints second, then the validated complete
Organization catalog snapshot is exposed. Standard
Organization definitions remain an immutable file-backed fallback rather than
being copied into every project. A project may overlay a `key@version` only
through the scoped definition API; compilation resolves that row first and
falls back to the checked-in definition when no row exists. A scoped
`retired` row can therefore hide one fallback revision without deleting or
rewriting the seed. Referenced project revisions must be `active`; their
canonical hashes and the transitive Team/Role/Workflow/Handoff/Policy closure
are checked and bound into the mutation digest.

Definition changes use preview-bound one-shot project grants. Validation,
archive preview and reconcile preview compute canonical definition/policy
hashes and an exact mutation or plan digest. Apply revalidates those bindings,
uses optimistic parent/current revision checks and consumes the grant in the
same Unit of Work as the new revision, operation receipt and audit outbox
event. Reconciliation only creates a new definition revision; it reports
active assignment impacts and preserved snapshot hashes but never edits an
Organization Instance snapshot. This separates validation, authorization,
persistence and HTTP adaptation (SRP/DIP) while keeping the Hub the sole
control plane.

`acceptance_fixtures` are read-only conformance metadata and are never
materialized as organization instances during seeding. Although
`test_only_fixtures` are physically co-located with the organization fragment
for deterministic review, a production adapter must omit them completely.
Only an explicitly injected test-harness catalog may expose them. The harness
mints a fresh, request-bound admission exception; no reusable exception grant
is stored in a fixture.

## Standard and custom composition

The production standard is a parameterized band, not six presets. Its baseline
is two Product Delivery teams and one Portfolio, Research and Platform team.
Quality/Security/Release, Architecture and PoC are activated in that order for
six, seven and eight teams; nine and ten scale the repeatable Delivery group.
Eight is the default and the only full acceptance reference.

The eight-team reference expands to:

| Projection | Exact count |
| --- | ---: |
| Organization root | 1 |
| Coordination units | 1 |
| Value streams | 3 |
| Team instances | 8 |
| Hierarchy `contains` edges | 12 |
| Active `organization` edges | 8 |

The hierarchy is derived from `parent_unit_ref`. Cross-team dependencies,
governance, reviews and handoffs are separate namespaced relations. This
separation lets the UI project the same revision either as a hierarchy or as a
graph without treating layout state as domain state.

Standard mode accepts only `team_count` in the declared five-to-ten band.
Custom mode accepts explicit Team Blueprint counts plus an admission
exception. Custom N is bounded by the effective deployment limit profile, not
by a hard-coded business maximum of ten. The four two-/three-team definitions
are test cases for custom admission and capability-gap diagnostics, not
production presets.

### Production Custom-N admission boundary

Admission issuance is a Hub command, separate from the pure compiler. The
public endpoint requires project `MANAGE`, a request idempotency key, exact
Team Blueprint counts, a bounded reason and a 60–3600 second TTL. The command
service resolves the current Organization Blueprint and limit profile, invokes
the same pure `OrganizationCustomCompositionService` used by compilation and
persists one `OrganizationAdmissionExceptionDB` row. Its request digest binds
tenant, project, principal, definition key/version/revision, limit-policy hash,
canonical composition digest, reason and TTL. Identical retries project the
same row; changed reuse of the idempotency key conflicts.

`SqlOrganizationAdmissionPolicy` is a narrow read-only compiler port. It
accepts only an unexpired, unrevoked `issued` row matching exception ID,
tenant/project, principal, definition key/version/revision, policy hash,
composition digest and normalized counts. Compile therefore remains
side-effect free and may create several candidate dry-runs while the exception
is live. Every compile token additionally binds the selected exception and
counts to its newly allocated candidate Organization ID and plan digest.

Consumption belongs to the instantiation transaction, not the compiler.
`GrantConsumingOrganizationUnitOfWork` conditionally updates the matching
exception `issued -> consumed`, conditionally consumes the distinct
plan-bound precreation admin grant, and then exposes the same transaction to
aggregate materialization. Zero or multiple matching rows fail closed before
writes. Rollback restores both grants and every aggregate table. Commit makes
the exception unusable for another candidate; an already-applied operation may
only be returned through the identical Instantiate idempotency replay. This
keeps authorization, compilation and persistence responsibilities separated
(SRP/DIP) while making the one-shot decision atomic.

## Role and Team Blueprint boundaries

Role templates carry mission, scope, inputs, outputs, decisions, handoffs,
capability/context constraints, verification and escalation. Governance is
structured data outside free-form prompts. Shared appendixes express common
engineering, research, security, platform and independent-review behavior
without duplicating policy prose.

Role slots bind a versioned template to cardinality, capability compatibility,
write requirements, optional overlays and separation-of-duties constraints.
Team Blueprints group these slots with artifact contracts, a workflow reference,
policies and capacity defaults. Scrum accountability remains one of Product
Owner, Scrum Master or Developer; specializations do not create additional
Scrum accountabilities.

## Grounding and handoffs

Grounding is exact membership in the allowlist supplied for the current Hub
assignment. A string that merely matches the `SRC_`/`RUN_` format is not
trusted, and workers cannot mint source identifiers. Missing or unknown
references remain unverified or failed.

`team_handoff.v1` binds producer and consumer organization/unit/team/slot,
source task, versioned artifacts and digests, acceptance checks, due/SLA,
correlation and idempotency information. The consumer returns a structured
`accepted`, `rejected` or `needs_changes` decision. Only Hub-verified artifact
versions with assignment-allowed provenance can satisfy a gate; direct worker
calls are not a handoff transition.

## Worker task proposals

`task_followup_proposal.v1` is an untrusted result contract, not a task or queue
record. It binds proposal/idempotency identity, source goal/task/category
items, organization/unit/team/role slot, assignment and dispatch lease, role
template revision, proposal-policy hash and payload digest. Suggested roles,
teams or agents are non-binding hints; direct worker addresses and queue
priority controls are absent.

`worker_todo_result.v1` and `worker_execution_result.v1` add an optional closed
`task_proposals` carrier. It contains either bounded inline proposals plus a
digest or a digest-bound artifact-version reference. Existing producers may
omit the field.

The effective role/slot policy defaults to deny and can only restrict the
source assignment's capabilities, tools, context, evidence allowlist, scope
and remaining budget. The Hub revalidates the exact policy hash, lease and
scope immediately before deciding. Only the Hub can classify a proposal,
amend a Planning Track, select a role/team/agent and materialize a task.
Proposal persistence, ingress validation, policy decision and routing remain
separate adapter responsibilities.

## SOLID boundary check

The schema, fragment loader, semantic validator, compiler, persistence adapter
and UI projection each have one responsibility (SRP). Consumers depend on
narrow catalog and policy ports rather than filesystem or database details
(DIP/ISP). Versioned definitions and additive result fields preserve existing
seed and worker-result consumers (OCP/LSP). Hidden task writes, implicit shared
state and prompt-granted permissions are explicitly excluded.
