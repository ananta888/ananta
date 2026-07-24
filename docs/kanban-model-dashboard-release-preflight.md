# Kanban Model Dashboard Release Preflight

The release preflight is a read-only readiness check for the seven-suite
Kanban model-dashboard evidence producer:

```bash
python3 scripts/run_kanban_model_dashboard_evidence_preflight.py
```

It does not execute a suite, create an artifact, or write commit-bound
evidence. A blocked preflight exits with status `2`; a ready preflight exits
with status `0`.

## Performance baseline boundary

Formal performance evidence uses only:

`config/test-profiles/kanban-model-dashboard/baselines/formal-performance-approved.v1.json`

The baseline must use schema
`ananta.kanban-model-dashboard.performance-baseline.v1`, set
`approval_status` to `approved`, identify a non-empty `approved_by`, and carry
a timezone-qualified `approved_at`.

The following artifact is review material only and cannot replace the approved
baseline:

`artifacts/test-gates/kanban-model-dashboard-performance-baseline-candidate.v1.json`

Its `candidate_unapproved` state therefore cannot yield passed release
evidence. Until organizational approval is represented at the reserved
approved-baseline path, the performance gate remains blocked exactly with
`baseline_approval_required`.

## Dirty worktree boundary

The preflight reads `HEAD` and the worktree state through non-mutating Git
commands. Any tracked or untracked source change is reported as
`uncommitted_candidate`. This prevents a dirty worktree from being described
as commit-bound passed evidence for `HEAD`.

In the current development state, the expected remaining boundaries are:

1. `uncommitted_candidate` (technical)
2. `baseline_approval_required` (operational)
