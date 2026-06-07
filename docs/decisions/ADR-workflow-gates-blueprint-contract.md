# ADR: Workflow Gates and Blueprint Contract

- **Status:** Accepted
- **Date:** 2026-06-07
- **Scope:** Deterministic workflow graph, gate enforcement, and the contract between a blueprint's `workflow` block and the planner / worker / queue layers.

## Context

A blueprint in `config/blueprints/standard/blueprints.json` describes
a team's roles and the artifacts the team owns. Today, a blueprint
is purely a *materialization* template: instantiating a blueprint
yields a TeamDB plus a flat list of starter TaskDB rows derived
from the blueprint's `artifacts` (kind=task).

The hub's planning layer does not know about a blueprint's intent
beyond "produce these tasks". The worker layer routes on
`TaskDB.assigned_role_id` and a hardcoded `pipeline_order` field,
which couples the queue layer to the *order* in which artifacts
were sorted at the time of writing the catalog. There is no way
to express:

- "Developer must not start until Planner's spec artifact is in."
- "Reviewer is a *gate* — downstream steps are blocked until they
  approve."
- "If the gate fails, fall back to human approval rather than aborting."

These are not implementation details; they are part of the team's
*contract* and belong in the blueprint, not in the planner or
queue code.

## Decision

1. **Workflow block is a first-class property of a blueprint.** The
   optional `workflow` property in `seed_blueprint_catalog.v1` is
   the single source of truth for the deterministic workflow graph
   a team follows. The JSON schema (`schemas/blueprints/seed_blueprint_catalog.v1.json`)
   defines the wire shape; the catalog normalizer
   (`SeedBlueprintCatalog._normalize_workflow`) validates the DAG,
   the role references, and the gate-checks implication.

2. **Workflow modes are explicit, not inferred.** A workflow has a
   `mode` ∈ {`off`, `direct`, `gated`, `strict_gated`}:
   - `off` — workflow block is present for documentation only; the
     planner materializes tasks from `artifacts` exactly as before
     (WFG-021 backward-compat path).
   - `direct` — planner materializes steps in topological order but
     does not enforce gates. Workers can pull tasks out of order.
   - `gated` (default for new blueprints) — gates block downstream
     steps until they pass. If a gate's `failure_policy` is `block`,
     the queue halts the workflow; if `skip`, downstream steps are
     marked skipped; if `manual`, a human approval task is raised.
   - `strict_gated` — same as `gated`, plus a goal cannot be
     marked complete while any gate is pending.

3. **Steps are an explicit DAG, not an ordered list.** A step's
   `depends_on` is the authoritative predecessor declaration. The
   catalog normalizer rejects cycles (Kahn's algorithm) and
   unknown-dependency references at seed-reconciliation time. This
   means a workflow cannot be mis-serialized: a step without
   `depends_on` is treated as a root, not as "position 0".

4. **Gates are steps, not a separate concept.** A step with
   `gate: true` carries a `checks` object that the gate-check engine
   evaluates. This keeps the DAG uniform: a gate is just a step
   whose `task_kind` is `gate_review` and whose `failure_policy`
   controls the propagation behaviour.

5. **The `pipeline_order` field on TaskDB is decoupled from workflow
   position.** `pipeline_order` becomes a worker-routing hint for
   the queue layer (legacy + ad-hoc ordering) but is no longer the
   authoritative execution order. Workflow steps carry their own
   `sort_order` and topological sequence (WFG-010).

   Concretely, when both signals are present on a single task:

   | Signal | Source | Authority | Used for |
   |--------|--------|-----------|----------|
   | `workflow.steps[].depends_on` | blueprint | **authoritative** | execution order, gate blocking, depends_on in TaskDB |
   | `workflow.steps[].sort_order` | blueprint | tie-break only | stable ordering when a step has no `depends_on` |
   | `TaskDB.pipeline_order` | legacy catalog | **fallback** | queue ordering for tasks with no workflow_step block |
   | `TaskDB.depends_on` | planner | **authoritative** | queue-side blocking (mirrors workflow depends_on) |

   A task with `worker_execution_context.workflow_step` set always
   follows the workflow DAG, never the legacy `pipeline_order`. The
   planner materializer (WFG-007) and the queue reconciler (WFG-013)
   enforce this precedence. A regression test in
   `tests/test_planning_track_task_integration_service.py`
   (`test_workflow_steps_take_precedence_over_pipeline_order`) locks
   it in.

6. **The hub remains the owner of the workflow contract.** Workers
   never read the blueprint's workflow block directly; the planner
   materializes it into TaskDB rows, audit events, and gate state
   rows. This preserves the hub-worker boundary documented in
   `AGENTS.md`.

7. **Backward compatibility is enforced via a 12-month shim
   strategy.** Blueprints without a `workflow` block behave exactly
   as before: the planner materializes artifacts-only tasks, the
   queue orders them by `pipeline_order`. WFG-021 documents the
   compatibility matrix in detail; the `ANANTA_WORKFLOW_MODE`
   environment variable (WFG-003) lets operators opt out of
   workflow enforcement globally for a deployment.

## Relationship model

1. **Blueprint layer:** owns the team type, roles, artifacts, and
   the optional `workflow` block. Source of truth for the contract.
2. **Catalog normalizer layer:** validates the workflow block at
   seed-reconciliation time (DAG, role references, gate-checks
   implication). Rejects malformed blueprints before they reach the
   planner.
3. **Workflow definition service layer:** materializes the validated
   workflow into `BlueprintWorkflowStepDB` rows (WFG-005) plus
   default gate rows (WFG-008) at blueprint-load time. Owns no
   execution logic.
4. **Planner / materialization layer:** reads
   `BlueprintWorkflowStepDB` rows in topological order, creates
   TaskDB rows with the right `task_kind` and `gate_step_id`
   references, and emits handoff events (WFG-007, WFG-015).
5. **Worker / queue layer:** pulls tasks from the queue respecting
   gate state (WFG-013), updates the gate-check engine (WFG-011)
   with deterministic checks (WFG-012), and applies the configured
   `failure_policy` (WFG-014).
6. **UI layer (TUI / Angular):** renders the workflow graph and
   per-step status (WFG-022, WFG-023). Read-only with respect to
   the contract; human approval is a first-class action
   (WFG-024).

## Compatibility behavior

- Existing blueprints without a `workflow` block: unchanged
  behavior, artifacts-only materialization, no gate enforcement.
- Existing blueprints with a `workflow` block (future): the catalog
  normalizer rejects malformed workflows at seed time, so a broken
  workflow is loud, not silent.
- `ANANTA_WORKFLOW_MODE=off` (deployment-wide override) skips
  workflow materialization for *all* blueprints, including those
  with a workflow block. This is the operator-level kill switch
  (WFG-003).
- The `team_blueprint_service` module stays as a 12-month re-export
  shim so that the 5-module split (WFG-029) and the workflow layer
  can be adopted incrementally. The CI detector
  `scripts/check_shim_imports.py` reports the remaining consumer
  count.

## Consequences

Positive:
- A team's contract is declarative, lives in the catalog, and is
  validated at seed time. The planner and queue become deterministic
  with respect to the contract.
- Gates are first-class, not a special case in worker code. Adding
  a new gate is a JSON change, not a code change.
- Topological ordering is computed from the DAG, not from sort
  order at write time. The catalog can be re-sorted without
  changing the workflow.
- Hub-worker boundary is preserved: workers receive TaskDB rows
  with `gate_step_id` references and read gate state from a
  dedicated service, not from the blueprint JSON directly.

Negative / accepted risks:
- The workflow block is a new schema surface. Operators must learn
  the `mode`/`task_kind`/`gate` vocabulary. Mitigated by WFG-020
  (blueprint-admin docs + examples).
- Strict-gated mode blocks goal completion while gates are pending.
  This is a behaviour change for any blueprint that opts in. Mitigated
  by the `off`/`direct` opt-out and the deployment-wide
  `ANANTA_WORKFLOW_MODE` override.
- Cycle detection in the catalog normalizer adds a one-time O(V+E)
  pass at seed time. Negligible at current catalog sizes (V<20).
