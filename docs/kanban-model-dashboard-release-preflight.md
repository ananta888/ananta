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
`ananta.kanban-model-dashboard.performance-baseline.v1` and carry a valid
attestation from the versioned hub policy at
`config/test-profiles/kanban-model-dashboard/baseline-approval-policy.v1.json`.
The policy verifies the candidate commit, bounded worktree state, profile and
source hashes, runtime compatibility metadata, absolute budgets, and freshness.
Missing or modified attestation data fails closed.

The following artifact is policy input only and cannot replace the approved
baseline:

`artifacts/test-gates/kanban-model-dashboard-performance-baseline-candidate.v1.json`

Its `candidate_unapproved` state therefore cannot yield passed release
evidence. Promote it automatically after the four diagnostics and candidate
generation have completed:

```bash
python3 scripts/performance/kanban_baseline_approval_policy.py
```

No human response, UI action, or external review board is required. An
interactive review may be added as an optional co-signature, but is never a
technical prerequisite. If the policy cannot prove all required conditions,
promotion fails and the performance gate remains blocked with
`baseline_approval_required` or `baseline_approval_invalid`.

## Dirty worktree boundary

The preflight reads `HEAD` and the worktree state through non-mutating Git
commands. Any tracked or untracked source change is reported as
`uncommitted_candidate`. This prevents a dirty worktree from being described
as commit-bound passed evidence for `HEAD`.

After a policy-approved baseline and its candidate are committed, the
read-only preflight is expected to report `ready`. A dirty source worktree is
still reported as `uncommitted_candidate`.
