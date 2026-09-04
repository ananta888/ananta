# Operating research training

Research training is disabled by default. A bounded local CPU deployment uses:

```text
ANANTA_RESEARCH_TRAINING_ENABLED=true
ANANTA_RESEARCH_TRAINING_MODE=local
ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED=false
ANANTA_RESEARCH_TRAINING_POLICY_PATH=config/research-training/policy.v1.json
ANANTA_RESEARCH_TRAINING_SAFETY_PATH=config/research-training/safety.v1.json
```

Enable only the stage capabilities needed in the safety policy. RL, multi-GPU and generated-code evaluation have
independent switches. Keep Hub state, admitted datasets, Worker result ingress and artifact storage on persistent,
separate volumes. Worker input is read-only; output is the only writable bind.

The isolated CPU Worker can be built with:

```bash
docker build -f docker/compose-next/Dockerfile.research-training-worker \
  -t ananta-research-training-worker:local .
```

Run it through `docker/compose-next/compose.research-training.yml` after setting the required input/output roots and
the scheduler-attested `ANANTA_RESEARCH_REPOSITORY_REVISION`, `ANANTA_RESEARCH_IMAGE_DIGEST` and
`ANANTA_RESEARCH_HARDWARE_PROFILE_DIGEST`. The Worker compares those values and its actual Python, Torch and CUDA
versions with the immutable assignment before execution. The service has no network, runs non-root with all
capabilities dropped, uses a read-only root filesystem and bounded CPU, memory, PIDs and tmpfs. An assignment is
created by the Hub; never hand-author evidence IDs.

Operational recovery is automatic: leases receive heartbeats, expiration creates a bounded retry, deterministic
input failures are terminal, and SIGTERM/SIGUSR1 writes an atomic digest-bound checkpoint before the Hub requeues a
compatible attempt. Quota is reserved before execution and finalized on atomic publication. Retention deletes only
unpinned ephemeral leaves and refuses referenced parents.

Local gates:

```bash
python scripts/check_research_training_boundaries.py
pytest -q -n 2 tests/research_training
cd frontend-angular
npx vitest run src/app/features/model-training/training-wizard/research-training-workbench.component.spec.ts \
  src/app/features/model-training/model-training-api.service.spec.ts
npm run build
```

GPU absence is not an error for the CPU gate. A requested GPU profile without current inventory/evidence returns an
explicit unavailable/unverified reason. Synthetic/test evidence never promotes production. No failure can be changed
to verified by an interactive approval.

## Rollout and rollback

`config/research-training/rollout.v1.json` remains default-off and kill-switched. Phases progress automatically only
after all declared gates pass: schema/dry-run, tiny CPU, single GPU, multi-GPU, then optional RL. Rollback disables
only research admission/runtime and leaves LoRA plus production routes unchanged. Upstream nanochat remains
`review_only`; no source is synchronized or executed implicitly.
