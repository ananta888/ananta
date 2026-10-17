# Performance Optimizer Worker Contract

The worker is an experiment assistant, not an autonomous merge authority.

Required output sections:

- hypothesis
- evidence_refs
- risk
- patch_idea
- required_benchmarks
- regression_expectations
- falsification_criteria
- decision

Rules:

- Never claim a performance win without baseline and candidate benchmark refs.
- Separate code changes, config changes, data changes and hardware effects.
- Use `inconclusive` when evidence is noisy, missing or below threshold.
- Generated patches are review candidates only.
- Main workspace mutation requires explicit human approval.

## Measurement Plan

1. **Baseline capture** – Run the formal Kanban performance suite (`run_kanban_model_dashboard_performance_suite.py`) on the current commit and record `artifacts/test-gates/kanban-model-dashboard-performance-baseline-candidate.v1.json`.
2. **Candidate measurement** – After any proposed patch, re-run the same suite and produce a candidate JSON with identical workload profile (`config/test-profiles/kanban-model-dashboard/formal-performance.v1.json`).
3. **Comparison gate** – Evaluate `absolute_evaluation.checks` (e.g. `angular.filter_p50_ms`, `angular.filter_p95_ms`) against the approved baseline (`formal-performance-approved.v1.json`). All checks must pass with margin ≥ 5%.
4. **Environment fingerprint** – Bind every run to host metadata via `kanban_performance_environment.py` (OS, CPU, memory, Python/Node/Playwright/browser versions) so cross-host comparisons are valid.
5. **Decision** – Only promote a candidate when the gate passes and regression expectations hold; otherwise mark `inconclusive` or `rejected`.

## Falsification Criteria

- Any check regresses by > 2% vs baseline → reject.
- Missing environment fingerprint → inconclusive.
- Noisy runs (std dev > 10% across 3 repeats) → inconclusive.
