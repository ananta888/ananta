# Dendritic-memory experiment runbook

Safe defaults:

- `ANANTA_DENDRITIC_MEMORY_ENABLED=false`
- `ANANTA_DENDRITIC_MEMORY_MODE=disabled`
- `ANANTA_DENDRITIC_MEMORY_RUNTIME_ENABLED=false`
- `ANANTA_DENDRITIC_MEMORY_AUTOMATIC_ACTIVATION_ENABLED=false`

Use `mode=mock` for deterministic CPU-only control-plane checks. Mock packs are
`executable=false` and cannot enter runtime. Local mode must use an admitted
read-only local model catalog; it never downloads model code or weights.

Diagnostic reason codes distinguish disabled policy, missing Worker
capability, invalid configuration, stale attempt, pack tampering, evaluation
failure and runtime-gate failure. Cancellation is monotonic and completes
without an operator acknowledgement. Revoke requires unloading any active
route first; deactivation restores the base-model route.

The Hub reconciler automatically fails expired queued/running attempts and
finalizes stale cancellation requests. Registry mutations are idempotent and
append tenant-scoped audit events for import, composition, approval, runtime
activation, rollback, revoke and deletion. Deletion requires a rejected or
revoked pack with no active route or live child composition. Runtime loading
revalidates the model snapshot, manifest digest and every local file hash;
failure leaves the unchanged base model active.

Focused checks:

```bash
.venv/bin/python scripts/check_dendritic_memory_boundaries.py
.venv/bin/python -m pytest -p no:cacheprovider tests/dendritic_memory
cd frontend-angular && npx vitest run src/app/features/model-training/training-wizard/dendritic-memory-workbench.component.spec.ts
cd frontend-angular && npx playwright test tests/model-training.spec.ts --grep dendritic
```

The opt-in real local-model test requires
`ANANTA_DENDRITIC_TEST_MODEL_PATH` (a closed, local safetensors fixture) and
`ANANTA_DENDRITIC_TEST_MODEL_SHA256`. Without both it skips with an explicit
reason and never downloads a model.

Release remains blocked until all P0 checks, CI, three seeds, two task
families, security clearance, rollback/revoke/deletion runs and exact
assignment-bound `SRC_*`/`RUN_*` evidence pass. The gate is fully automatic and
does not require Human-in-the-Loop.

The release gate accepts a Hub Evidence Registry binding and verifies exact
task, repository revision and scope. Legacy explicit allowlists remain
available for compatibility. Registry issuance itself is no longer a blocker;
the missing facts are a real admitted model/dataset experiment over three
seeds and two task families plus a practical staging lifecycle run. The
optional local-model test intentionally skips when no immutable safetensors
snapshot is supplied and can never be counted as a passed release gate.
