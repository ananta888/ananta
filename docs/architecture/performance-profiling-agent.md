# Performance Profiling Agent

Ananta's performance agent is a hub-controlled experiment pipeline.

Flow:

1. Create an experiment plan.
2. Run a baseline benchmark through the native worker runtime.
3. Parse profiling output into profile observations.
4. Resolve hotspots and build falsifiable hypotheses.
5. Build a bounded context package.
6. Apply candidate patches only in a sandbox.
7. Run candidate benchmarks and regressions.
8. Compare baseline and candidate artifacts.
9. Produce a human-review report.

The hub owns orchestration and policy. Workers may propose patches and analysis,
but do not orchestrate other workers and do not merge changes.

Negative example: a patch that improves wall time but changes output is rejected.
The comparison artifact may show an improvement, but the regression gate blocks
`passed`.

Good optimization proposal checklist:

- baseline_run_id and candidate_run_id are present
- primary metric and threshold are explicit
- regression result is present
- caveats mention noise or hardware dependence
- affected files are tied to evidence
- rollback is possible by discarding the sandbox
