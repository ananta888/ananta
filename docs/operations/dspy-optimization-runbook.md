# DSPy optimization operations

## Safe states

- disabled: reject admission; the rest of Ananta remains operational.
- unavailable: enabled Hub has no compatible worker; keep baseline active.
- degraded: worker version/capability mismatch; reject new runs.
- failed/cancelled: terminal run, never promote its candidate.
- blocked release: local checks may pass, but missing allowed evidence prevents rollout.

## Automatic recovery

| Detection | Automatic action |
| --- | --- |
| stale attempt or duplicate finalization | reject by attempt/revision fence |
| missing provider usage | fail the call; never treat cost as zero |
| call/token/cost/time limit | stop new calls and finalize failed/cancelled |
| worker loss | Hub retains admitted/running revision; retry requires a fresh attempt |
| retryable provider error | retry under the same logical request ID and consume call/retry budget |
| corrupt/unsafe program state | reject before artifact write |
| evaluation regression | keep baseline active |
| canary stop criterion | atomically roll back the known previous digest |
| incompatible DSPy version | capability becomes degraded |

Rollback is an immutable registry revision, not an artifact rewrite. The
operator can disable admission immediately through configuration; policy may
also stop and roll back automatically. Neither tests nor production recovery
requires a person to unblock a waiting workflow.

Do not delete state databases or content-addressed artifacts during an
incident. Preserve them for revision, fencing and provenance analysis. Runtime
files under `data/` are not source and must not be committed.

## Diagnosis and bounded actions

- A stuck admitted/running/cancelling job is reconciled with the Hub `recover`
  operation. An expired lease becomes terminal `dspy_worker_lease_expired`; it
  is never silently restarted under the stale attempt.
- `dspy_usage_missing`, role/call/token/cost exhaustion and concurrency denial
  stop new calls. Inspect digest-only operational telemetry; do not enable raw
  prompt logging.
- `dspy_price_profile_missing` is terminal. Provider-reported prices must not be
  copied into Ananta accounting; repair the Hub-owned price-profile binding.
- `dspy_retrieval_backend_unavailable` keeps the baseline active. Restore the
  authorized CodeCompass backend, then create a new run and evidence identity;
  do not reuse the failed result.
- `dspy_version_incompatible` requires the pinned lock/image or an isolated
  compatibility change. Never loosen the version check in production.
- A corrupt or unsupported prompt program triggers the native baseline
  fallback. Legal Hold and active promotion references prevent retention from
  deleting the candidate while it is investigated.
- Canary security, parse, latency or cost criteria call `stop_canary` with one
  of the plan-bound reason codes. The extra operator kill switch uses
  `operator_kill_switch`; both create a new immutable registry revision.
- Rollback is safe to automate and requires exact tenant, scope and expected
  revision. A conflict means the operator or automation must read the new
  revision and reevaluate, not overwrite it.

## Release and rollback package

Run `scripts/build_dspy_worker_sbom.py`, the focused test suite and
`scripts/run_dspy_optimization_release_gate.py`. Local success proves contracts,
lock, SBOM template, imports and containment only. CI builds the worker and
publishes its actual image digest as an artifact. Production release additionally
requires Hub-admitted dataset/provider/source bindings and a pre-reserved
production `RUN_*` covering quality, cost, recovery and rollback. Missing inputs
produce a bounded blocked result with the previous program still active.

Read `GET /api/dspy-optimization/provenance` or `ananta optimization provenance`
before and after rollback. The projection contains every immutable registry
revision and its evaluation, promotion-plan, active and previous digests; never
rewrite this history during incident recovery.
