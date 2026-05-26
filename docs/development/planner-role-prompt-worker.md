# Planner Role, Prompt, and Worker Notes

## Prompt template

Primary prompt template for planning track mode:

- `prompts/planning/track_planning.j2`

The template enforces:
- planner/track-only output (no patches/shell commands)
- JSON-only response (no markdown fences in final answer)
- schema contract and required fields
- explicit warning that nested `epics.tasks` is not the target model

## Required payload fields

Minimum required top-level fields (contract):

- `version`
- `owner`
- `track`
- `status_scale`
- `priority_scale`
- `risk_scale`
- `milestones`
- `tasks`
- `tasks_status_summary`

Minimum required task quality fields:

- `title`
- `status`
- `priority`
- `risk`
- `type`
- `acceptance_criteria`

Optional but commonly used task fields:

- `depends_on`
- `milestone_id`
- `progress_percent`

## Typical planner output errors

- prose-only output (`non_json_output`)
- malformed JSON (`invalid_json`)
- wrong root shape (`invalid_shape`)
- missing required fields (`missing_required_field`)
- missing/empty task acceptance criteria (`empty_acceptance_criteria`)
- nested `epics.tasks` instead of flat `tasks[]` (schema validation failure)

## Why `epics.tasks` is not the target model

Execution handoff and TUI filtering depend on a flat task list with stable task IDs.
Nested epic structures break deterministic status aggregation and direct task materialization (`plan_task_id -> internal task`).

## `tasks_status_summary` calculation

`tasks_status_summary` is deterministic and derived from `tasks` (+ milestone and critical path references), including:

- `total`
- `by_status`
- `progress_percent_done`
- `by_priority`
- `by_risk`
- `critical_path` aggregate
- `milestones` aggregate

If planner-provided summary is inconsistent, the pipeline recomputes and repairs it before persistence.

## Derived summary policy (single source of truth)

- `tasks[]` is the single source of truth.
- Planner may output summaries, but hub/validator recalculates and overwrites derived blocks.
- Persisted envelope/artifact metadata includes:
  - `summary_recalculation_status`
  - `old_summary_hash`
  - `new_summary_hash`
  - `repaired_fields`

Task progress semantics:

- `todo -> progress_percent=0`
- `done -> progress_percent=100`
- `in_progress|partial -> progress_percent=1..99`
- `blocked -> progress_percent=0..100`

## Incorrect vs corrected summary example

Worker output (incorrect):

```json
{
  "tasks": [{"id":"T1","status":"done","priority":"P1","risk":"high","type":"backend","acceptance_criteria":["ok"]}],
  "tasks_status_summary": {"total": 999}
}
```

Persisted output (corrected by validator):

```json
{
  "tasks_status_summary": {
    "total": 1,
    "by_status": {"todo":0,"in_progress":0,"partial":0,"blocked":0,"done":1}
  },
  "summary_recalculation_status": "repaired"
}
```

## Local planning tips (LM Studio / Ollama)

- Keep context focused: grant only relevant source artifacts.
- Prefer smaller context windows for weaker local models.
- If model tends to return prose, reinforce JSON-only behavior and validate with mock tests.
- Use `:plan track --from-goal <goal-id>` and verify `planning_status=valid` before adopting.
- For execution handoff, adopt then run `:plan track execute-next`.
