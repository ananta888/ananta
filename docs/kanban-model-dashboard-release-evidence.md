# Kanban/model dashboard release evidence

Release evidence uses schema
`ananta.kanban-model-dashboard.evidence.v1` and consists of exactly seven
suites:

`contract`, `backend`, `angular`, `tui`, `security`, `accessibility`, and
`performance`.

## Candidate boundary

Evidence is a CI/release artifact produced **after** the candidate commit
exists. It is evaluated against the exact full candidate SHA. Evidence files
under `artifacts/e2e/kanban-model-dashboard/` must not be inputs to, or tracked
files in, that same candidate commit. This prevents a file from claiming its
own SHA transitively.

The producer verifies:

- the checked-out commit equals the requested candidate SHA;
- every allowlisted input is a regular, non-symlinked file;
- every working input SHA-256 equals the corresponding candidate Git blob;
- every command is the fixed argv allowlist and is executed without a shell;
- every command exits with zero and satisfies its suite-specific result
  contract;
- the final evidence is written by atomic replacement.

Missing tools, inputs, candidate blobs, or approved performance baselines
block production. Command or semantic-result failures produce failed
evidence. Neither state can pass the release gate.

## Performance interface

Performance is aggregated by
`scripts/performance/run_kanban_model_dashboard_performance_suite.py`.
Successful release evaluation exclusively allowlists the organizationally
approved baseline:

`config/test-profiles/kanban-model-dashboard/baselines/formal-performance-approved.v1.json`

Generated baseline candidates under `artifacts/test-gates/` are never accepted
as approved baselines. The formal result must have exit code zero,
`status=passed`, `release_evidence=true`, `formal_gate_eligible=true`, no
blockers, and `commit.sha` equal to the candidate SHA.

## Invocation

```bash
python3 scripts/run_kanban_model_dashboard_evidence.py \
  --candidate-sha <full-candidate-sha>

python3 scripts/run_kanban_model_dashboard_release_gate.py \
  --commit-sha <full-candidate-sha>
```

Do not commit the newly produced evidence into the candidate it evaluates.
