# Workflow Gates Architecture

This document is the architectural reference for the workflow / gate
layer introduced by the WFG-001..028 plan. It complements the
[ADR-workflow-gates-blueprint-contract](../decisions/ADR-workflow-gates-blueprint-contract.md)
and assumes familiarity with `AGENTS.md` and the hub-worker model.

## Concepts

| Term | Definition |
|------|------------|
| **role** | A named responsibility inside a team (e.g. `planner`, `developer`, `scrum_master`). A role is a *type*, not a worker. |
| **step** | A node in a workflow DAG. Has a unique `id`, a `role`, a `task_kind`, optional `depends_on`, optional `produces` / `consumes`, an optional `gate` flag, and optional `checks`. |
| **task** | A materialized `TaskDB` row produced by the planner from a step. Tasks are the *unit of execution*; steps are the *unit of contract*. |
| **gate** | A step with `gate: true` whose `checks` must pass before downstream steps can be picked up by the queue. A gate is a normal blocking task with additional check / approval logic. |
| **artifact** | A typed output (e.g. `execution_plan`, `verification_report`). Steps declare `produces` and `consumes` to make the artifact flow explicit. |
| **worker** | A registered `AgentInfoDB` that can execute tasks. Workers are routed by `role` + `task_kind` + `required_capabilities` + workflow-step-role mapping. |

### Why separate role from step from task

- A `role` answers "who is responsible?".
- A `step` answers "what happens, in what order, with what gate?".
- A `task` answers "what is the queue doing right now?".

Mixing these concerns is what made the old `pipeline_order` catalog
field both too rigid (no DAG, no gates) and too implicit (coupling
to the order in which artifacts were sorted at write time).

## The workflow contract

A blueprint's optional `workflow` block is a deterministic DAG of
steps. The shape is defined in
`schemas/blueprints/seed_blueprint_catalog.v1.json` and validated by
`SeedBlueprintCatalog._normalize_workflow`.

Minimal example:

```json
{
  "workflow": {
    "mode": "gated",
    "steps": [
      {"id": "plan",   "role": "planner",    "task_kind": "planning"},
      {"id": "review", "role": "scrum_master", "task_kind": "gate_review",
       "depends_on": ["plan"], "gate": true,
       "checks": {"plan_has_small_tasks": {}, "dependencies_acyclic": {}}},
      {"id": "build",  "role": "developer",  "task_kind": "coding",
       "depends_on": ["review"]}
    ]
  }
}
```

### Workflow modes

| Mode | Behaviour |
|------|-----------|
| `off` | The workflow block is documentation only. Planner materializes from `artifacts`, queue orders by `pipeline_order`. |
| `direct` | Workflow DAG is enforced for execution order but gates are advisory. |
| `gated` *(default)* | Gates block downstream steps until they pass. `failure_policy` controls propagation. |
| `strict_gated` | Like `gated`, plus a goal cannot complete while any gate is pending. |

Mode is resolved per blueprint with the deployment-wide override
`ANANTA_WORKFLOW_MODE` (WFG-003).

## pipeline_order vs workflow.steps

The legacy `config.json::pipeline_order` field and the new
`blueprint.workflow.steps[].depends_on` are *not* the same thing.

| Signal | Source | Authority | Used for |
|--------|--------|-----------|----------|
| `workflow.steps[].depends_on` | blueprint | **authoritative** | execution order, gate blocking, TaskDB.depends_on |
| `workflow.steps[].sort_order` | blueprint | tie-break only | stable ordering when a step has no `depends_on` |
| `TaskDB.pipeline_order` | legacy catalog | **fallback** | queue ordering for tasks with no workflow_step block |
| `TaskDB.depends_on` | planner | **authoritative** | queue-side blocking (mirrors workflow depends_on) |

When a task carries `worker_execution_context.workflow_step`, the
queue follows the workflow DAG and ignores `pipeline_order`. When
no workflow step is present, the queue falls back to
`pipeline_order` for legacy ordering.

This precedence is enforced by:

- `BlueprintPlanningAdapter._build_subtasks_from_workflow` (WFG-006):
  produces `depends_on` from the workflow DAG, never from
  `pipeline_order`.
- `PlanningTrackTaskIntegrationService.materialize_tasks` (WFG-007):
  persists `worker_execution_context.workflow_step` and copies
  `depends_on` from the step DAG.
- `choose_worker_for_task` (WFG-009): uses `workflow_step.role` to
  pick the worker, not the legacy role hint.

The regression test
`test_workflow_steps_take_precedence_over_pipeline_order` in
`tests/test_planning_track_task_integration_service.py` locks the
precedence in.

## Layer model

1. **Blueprint layer** — declares roles, artifacts, and the
   `workflow` block. Source of truth.
2. **Catalog normalizer** — validates the workflow block at
   seed-reconciliation time (DAG acyclicity, role references, gate
   checks implication). Rejects malformed blueprints.
3. **Workflow definition service** — materializes the validated
   workflow into `BlueprintWorkflowStepDB` rows (WFG-005) and
   inserts default gates (WFG-008). Owns no execution logic.
4. **Planner / materialization** — reads
   `BlueprintWorkflowStepDB` in topological order, creates
   `TaskDB` rows with the right `task_kind`, `gate` flag, and
   `depends_on`, and emits handoff events (WFG-007, WFG-015).
5. **Worker / queue** — pulls tasks respecting gate state
   (WFG-013), updates the gate-check engine (WFG-011) with
   deterministic checks (WFG-012), applies the configured
   `failure_policy` (WFG-014).
6. **UI (TUI / Angular)** — renders the workflow graph and per-step
   status (WFG-022, WFG-023). Read-only with respect to the
   contract; human approval is a first-class action (WFG-024).

## Where LLMs may and may not decide

The workflow graph is **deterministic and LLM-free**. LLMs may
contribute:

- Step descriptions and prompts.
- Acceptance criteria text.
- Artifact content (the `produces` output of a step).

LLMs may **not** contribute:

- The shape of the DAG.
- The `depends_on` edges.
- The `gate` flag and `checks` selection.
- The `failure_policy` resolution.

If a planner LLM proposes a different workflow, the catalog
normalizer rejects it at seed time. The workflow stays a
deterministic contract.

## See also

- [ADR-workflow-gates-blueprint-contract](../decisions/ADR-workflow-gates-blueprint-contract.md)
- [planning-blueprint-flow](planning-blueprint-flow.md) (WFG-002)
- `docs/standard-blueprints.md` for the workflow example catalog
- `tests/test_workflow_definition_service.py` for the DAG validation
  contract
- `tests/test_blueprint_planning_adapter.py` for the planner integration
