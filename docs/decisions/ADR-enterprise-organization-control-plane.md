# ADR: Enterprise organization blueprints remain Hub-controlled

- **Status:** Accepted
- **Date:** 2026-08-02
- **Scope:** Organization blueprints, multi-team planning, routing and management UI

## Context

Ananta needs to manage five to ten standard teams, custom bounded N-team
organizations and small two-/three-team test compositions. The same definition
must support a hierarchical management view and a graph view similar to the
Visual Process editor. Roles assigned to delegated work may discover follow-up
tasks, but the existing Hub–Worker boundary must not turn into worker-to-worker
orchestration.

## Decision

1. The portable source of truth is a strict, versioned Organization Blueprint.
   It references versioned Role Templates, Team Blueprints, workflows, policies
   and handoff contracts by stable `key@version` values.
2. The Hub validates and compiles a blueprint without writes. A separate
   application service materializes only an unexpired, digest-bound compile
   plan in one transaction.
3. The Organization hierarchy is represented by one parent link per unit. The
   cross-team graph is a separate namespaced relation set. Runtime relations
   are read-only overlays; presentation coordinates are per-user preferences
   and never alter domain topology.
4. The production standard is a data-driven five-to-ten band with eight teams
   as default. Custom N uses the same compiler and finite deployment limits.
   Four small compositions are injected test fixtures and are never seeded as
   production presets.
5. Every Organization Goal follows the two-stage planning contract: a
   research-grounded Category-Todo is promoted to revision-bound Planning
   Tracks before the Hub may materialize Tasks.
6. A Worker can submit only a closed proposal bound to its current assignment
   and lease. The Hub verifies, classifies, approves and routes it. A proposal
   is neither a Task nor permission to enqueue work.
7. Lifecycle, topology patches, Bundle imports, assignment changes and
   approvals require scoped, revision-bound grants and are auditable.

## Responsibility boundaries

- Catalog loading only assembles immutable definitions.
- Schema and semantic validation report deterministic diagnostics.
- Compilation expands parameters and computes a plan digest without writes.
- Repositories and units of work own persistence and transaction boundaries.
- Application services enforce scope, policy and lifecycle transitions.
- Flask routes authenticate, translate requests and serialize results.
- Angular and TUI clients render Hub read models and submit intents; neither
  client performs routing or orchestration.

This split protects SRP and dependency inversion: domain services consume
narrow ports instead of Flask, SQLModel or filesystem details. Versioned
contracts and additive endpoints preserve open/closed and substitutability for
legacy team-only consumers.

## Consequences

- Hierarchy and graph views always identify the exact definition revision and
  snapshot hash from which they were projected.
- Worker containers need no shared database state and cannot contact another
  worker to create work.
- Resizing, reparenting or removal of active nodes must declare drain, migrate
  or archive semantics before apply.
- The sole full browser/runtime acceptance flow uses the eight-team reference;
  small fixtures remain cheap contract and isolated integration coverage.
- Existing Team and Team Bundle v1 APIs remain available through guarded
  compatibility adapters.

## Rejected alternatives

- **Workers route follow-up work directly:** breaks queue ownership, least
  privilege and auditability.
- **One denormalized graph document as runtime state:** creates hot-row writes,
  weak referential integrity and unsafe concurrent edits.
- **Separate hard-coded services for each team count:** makes N scaling and
  catalog evolution brittle.
- **Client-side topology authority:** permits stale UI state to bypass Hub
  policy and revision checks.
