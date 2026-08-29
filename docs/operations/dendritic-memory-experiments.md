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

Focused checks:

```bash
.venv/bin/python scripts/check_dendritic_memory_boundaries.py
.venv/bin/python -m pytest -p no:cacheprovider tests/dendritic_memory
cd frontend-angular && npx vitest run src/app/features/model-training/training-wizard/dendritic-memory-workbench.component.spec.ts
```

Release remains blocked until all P0 checks, CI, three seeds, two task
families, security clearance, rollback/revoke/deletion runs and exact
assignment-bound `SRC_*`/`RUN_*` evidence pass. The gate is fully automatic and
does not require Human-in-the-Loop.
