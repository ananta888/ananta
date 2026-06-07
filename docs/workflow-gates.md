# Workflow Gates & Blueprint Orchestration

This document is the operator/admin guide for the WFG-001..028
workflow layer. It explains how a blueprint's
``workflow.steps`` block is materialised into tasks, how gate
steps block the next phase, and how the audit query surfaces
what is currently blocking a goal.

For a deep dive on the architecture decisions, see
`AGENTS.md`. For the planning-pipeline hard rules, see
`docs/planning-pipeline.md`.

---

## 1. Why a workflow block?

A blueprint that ships with only ``roles`` + ``artifacts`` is
*implicit* — the planner has to invent the execution order.
A blueprint that ships with a ``workflow.steps`` block is
*explicit* — the planner honours the author-declared DAG,
the gate engine blocks the next phase until the gate step
passes, and the audit query can show "step 2 of 5 blocked
on gate_review" in one round-trip.

The workflow block is OPTIONAL. Blueprints without one
continue to work via the legacy artifact-based subtask
creation path (see §6 below). New blueprints SHOULD ship
with a workflow block.

---

## 2. Schema

A workflow block lives at ``blueprint.workflow`` and is
pinned to the schema ``blueprint_workflow.v1``::

  {
    "schema": "blueprint_workflow.v1",
    "id": "scrum_opencode",          // stable id, no spaces
    "version": 1,                    // bump on breaking change
    "seed_artifact_keys": [...],    // optional, see §5
    "steps": [
      {
        "id": "implementation",      // unique within the workflow
        "role": "Developer",         // matches a role in the blueprint
        "task_kind": "coding",       // routing hint (WFG-009)
        "task_ref": "Vertical Slice Delivery",   // matches an artifact title
        "consumes": ["execution_plan", "workspace_sync_receipt"],
        "produces": ["code_changes", "verification_evidence"],
        "depends_on": ["sync"],      // optional, inferred from produces/consumes
        "gate": false,
        "checks": [],
        "failure_policy": "block"
      },
      {
        "id": "review_gate",
        "role": "Scrum Master",
        "task_kind": "review",
        "task_ref": "Review And Definition Of Done",
        "consumes": ["code_changes", "verification_evidence"],
        "produces": ["dod_signoff"],
        "gate": true,
        "gate_decision_policy": "all_artifact_refs_present",
        "checks": [
          {"name": "code_changes_present", "type": "file_exists", "ref": "code_changes"},
          {"name": "verification_evidence_present", "type": "file_exists", "ref": "verification_evidence"},
        ],
        "failure_policy": "block_until_human_approval"
      }
    ]
  }

### Field reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| ``id`` | str | yes | unique within the workflow. Alphanumeric + ``_`` only. |
| ``role`` | str | yes | Must match a role in the same blueprint's ``roles`` list. |
| ``task_kind`` | str | yes | Routing hint used by WFG-009's worker selection. |
| ``task_ref`` | str | yes | Must match an artifact's ``title`` in the same blueprint's ``artifacts`` list. The planner materialises this artifact into a task. |
| ``consumes`` | list[str \| dict] | no | Artifact KEYS the step must see (WFG-016). Strict allowlist. |
| ``produces`` | list[str] | no | Artifact KEYS the step writes. Tracked by WFG-016's blocker evaluator. |
| ``depends_on`` | list[str] | no | Step ids this step depends on. Inferred from ``consumes`` ∩ upstream ``produces`` if absent. |
| ``gate`` | bool | no, default false | Marks this step as a gate (WFG-011). |
| ``checks`` | list[dict] | required if ``gate`` | Built-in check specs. See §4. |
| ``failure_policy`` | str | required if ``gate`` | ``block`` \| ``block_until_human_approval`` \| ``fail_workflow`` (WFG-014). |

### ``consumes`` shape

Three input shapes are accepted (mirrors WFG-016):

  - ``["execution_plan", "task_breakdown"]`` — list of strings (keys)
  - ``[{"key": "execution_plan", "type": "plan"}]`` — list of dicts
  - ``{"design_doc": True}`` — ``True`` means **optional** consume

Whitespace-only keys are dropped. Duplicate keys dedup with
first-occurrence-wins.

### ``seed_artifact_keys``

A workflow may declare a list of artifact KEYS it expects
the goal graph to inject at materialisation time. These
seed keys are treated as ``satisfies=always`` by the
artifact-flow validator (WFG-016) so the workflow's
topological validation passes.

Example: Security-Review's threat_review step consumes
``code_changes`` — but the security blueprint does not
produce code, so the workflow declares::

  "seed_artifact_keys": ["goal_brief", "code_changes"]

The planner then injects these keys from the goal graph
when materialising the workflow's tasks.

---

## 3. Materialisation

The WFG-007 materializer is the single point that turns a
workflow's steps into runtime tasks. Algorithm:

  1. Topologically sort the steps (Kahn's algorithm, the
     ``topological_order`` method on
     ``agent.services.workflow_definition_service``).
  2. For each step in order, look up the artifact whose
     ``title == step.task_ref``. If missing, fail with
     ``workflow_step_task_ref_missing``.
  3. Persist a ``workflow_step_provenance.v1`` block on
     the materialised task's ``worker_execution_context``.
  4. Persist a ``workflow_handoff.v1`` event per dependency
     edge (WFG-015).
  5. Compute the artifact blocker (WFG-016) and stamp
     ``status_reason_details.missing_artifacts`` if any
     required consume is not yet produced.

---

## 4. Gate checks

A step with ``gate: true`` is a gate. When the gate step
finishes, the WFG-011 engine evaluates every entry in
``step.checks`` against the goal's artifact graph. Each
check has the shape::

  {"name": "code_changes_present", "type": "file_exists", "ref": "code_changes"}

The built-in check types are:

| Type | Pass condition |
|------|----------------|
| ``file_exists`` | An artifact whose KEY matches ``ref`` is present in the goal graph. |
| ``min_artifact_count`` | At least ``N`` artifacts with the given prefix exist. ``params``: ``{"prefix": "...", "min": 1}``. |
| ``goal_state`` | The goal's ``status`` matches an expected value. ``params``: ``{"expected": "active"}``. |

The decision is recorded on the gate task's
``verification_status['gate_decision']`` (WFG-012 contract).
The next workflow step is allowed to claim only when the
gate decision is ``passed`` or ``skipped``.

The ``gate_decision_policy`` field controls the *combiner*:

  - ``all_must_pass`` — every check must pass. Default.
  - ``all_artifact_refs_present`` — every ref in the gate's
    ``consumes`` must be present in the goal graph.
  - ``any_must_pass`` — at least one check must pass.

A typo or unknown policy is **not** fatal; the engine
falls back to ``all_must_pass`` and logs a warning. This is
a deliberate design choice: a misconfigured gate must not
silently allow work through.

---

## 5. Failure policies

| Policy | Effect |
|--------|--------|
| ``block`` | Subsequent steps are blocked from claiming. The goal stays in its current state. |
| ``block_until_human_approval`` | Same as ``block`` plus the hub raises a human-approval event (WFG-024). The TUI shows a banner. |
| ``fail_workflow`` | The whole workflow is marked failed. Subsequent steps are cancelled. |

Invalid ``failure_policy`` values fall through to
``block`` (not raise) so a typo cannot deadlock the
workflow. The reason is recorded on the gate task's
``status_reason_details['invalid_failure_policy_fallback']``
key.

---

## 6. Backward compatibility (legacy blueprints)

Blueprints that ship WITHOUT a ``workflow`` block (e.g. the
plain ``Scrum``, ``Kanban``, ``Research``, ``Release-Prep``,
``Research-Evolution`` blueprints in the standard catalog)
continue to work. The materializer in
``planning_track_task_integration_service`` detects the
absence of the workflow block at materialisation time and
falls back to the legacy artifact-based subtask path:

  - The artifact list (``artifacts``) becomes the task list
    directly, in ``sort_order`` order.
  - The depends_on graph is inferred from the artifact's
    ``depends_on`` field, defaulting to empty.
  - No gate engine. No handoff events. No artifact-flow
    enforcement.

The audit query (WFG-017) detects this mode and reports
``workflow_block: legacy`` in the response so the TUI / UI
can label these goals as "non-gated".

The WFG-021 commit adds explicit migration tooling
(see ``docs/blueprint-migration.md`` for the playbook)
that takes a legacy blueprint and emits a v1 workflow
block. It is a code-generation step, not an automatic
migration — the blueprint author must sign off on the
generated gates.

---

## 7. Auditing a running goal

Two ways to inspect a running workflow:

### 7.1 HTTP API

```
GET /goals/<goal_id>/workflow-status
GET /goals/<goal_id>/workflow-status?debug=1
```

The first response is the full
``workflow_status.v1`` schema (steps, blockers, handoff
events, audit log). The second returns a compact
multi-line text summary suitable for a TUI rendering
or a paste into a chat.

### 7.2 TUI

In the TUI chat:

```
:workflow status <goal_id>
```

Renders the same debug text the ``?debug=1`` HTTP query
returns. See ``docs/tui-commands.md`` for the full command
list.

---

## 8. Examples

The four standard workflows in
``config/blueprints/standard/blueprints.json`` are the
canonical examples:

| Blueprint | Steps | Gate step | Failure policy |
|-----------|-------|-----------|----------------|
| ``Scrum-OpenCode`` | intake → cascade → sync → implementation → review_gate | review_gate | block_until_human_approval |
| ``Code-Repair`` | triage → fix → regression | regression | block_until_human_approval |
| ``TDD`` | behavior → red → patch → refactor | refactor | block_until_human_approval |
| ``Security-Review`` | threat_review → control_validation → findings_gate | findings_gate | block_until_human_approval |

To add a new workflow, copy one of these and adjust the
step ids, consumes, produces, and gate checks. The
validation tests in
``tests/test_blueprint_workflow_catalog.py`` enforce
the invariants across all standard blueprints.

---

## 9. Troubleshooting

### "goal materialises but stays in 'todo'"

  - The artifact-flow validator (WFG-016) likely reports
    a ``missing_consumes``. Check the audit query's
    ``steps[].missing_consumes`` list.
  - The gate step's checks are failing. Look for
    ``status_reason_code = gate_failed`` on the gate task.

### "blueprint import fails with workflow_violation"

  - The ``topological_order`` of the workflow has a
    cycle. The planner refuses to materialise it.
    Reorder the steps so every step's ``depends_on``
    points to an earlier step.

### "TUI shows 'no workflow block' but blueprint has one"

  - The blueprint's ``workflow`` block is not at the
    expected path. The loader expects
    ``blueprint['workflow']``; legacy ``blueprint['workflow_block']``
    is no longer accepted. See WFG-021 for the
    migration tool.
