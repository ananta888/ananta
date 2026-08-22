# HRM experiment runtime boundary

The HRM workbench is default-disabled and preserves Ananta's Hub-worker model.
The Hub owns policy, admission, tasks, leases and result acceptance. The normal
Worker process only adapts a Hub-delegated `hrm_experiment` task. It cannot
create tasks and cannot call another Worker.

Actual experiment code runs in `hrm-experiment-runner`. Communication is a
bounded length-prefixed JSON protocol over a Unix-domain socket. The runner has
`network_mode: none`, a read-only root filesystem, no Linux capabilities,
`no-new-privileges`, bounded PID/CPU/memory limits and ephemeral scratch space.
The runner rejects requests unless the complete Hub authority binding, schema
digest, payload digest, admission digest, runtime identity and resource limits
match.

The Compose overlay requires an already-created internal control network named
`ananta-hrm-control` by default. The Hub and HRM Worker may share that control
network; the runner never joins it. Set `ANANTA_HRM_RUNTIME_IMAGE_DIGEST` to the
approved lowercase SHA-256 of the built runner image before activating the
`hrm-experiments` profile. An image tag is not accepted as attestation.

Dataset and checkpoint admission is Hub-side. Artifact bytes are inspected
behind `HrmArtifactInspectionPort`; only verified artifact references and
verified Safetensors metadata can reach `HrmAdmissionRepositoryPort`. Direct
filesystem paths and network URLs are rejected.

## Operator and user workflow

HRM experiments remain disabled unless `HRM_EXPERIMENTS_ENABLED=true` is set
on the Hub and `ANANTA_HRM_EXPERIMENT_WORKER_ENABLED=true` is set on the
selected Worker. Enabling them does not bypass task assignment, WorkerJob,
slot-lease, service-scope, or tenant/project checks.

Apply database revision `d4f6a8c0e2b5` for the HRM projections and revision
`e5a7b9d1f3c6` for durable mutation-idempotency receipts before enabling the
surface. Deploy `docker-compose.hrm-experiments.yml`; the networkless runner
communicates with its Worker only through the mounted Unix socket. The Worker
refreshes its short-lived capability at the Hub. Missing or stale capability
makes preflight and execution authorization fail closed.

The public client reads its bearer token only from a file and rejects cleartext
HTTP except for loopback development:

```bash
python scripts/hrm_experiments_client.py \
  --base-url https://ananta.example \
  --token-file /run/secrets/ananta-user-token \
  capabilities
```

Mutating commands require a stable, caller-owned idempotency key:

```bash
python scripts/hrm_experiments_client.py \
  --base-url https://ananta.example \
  --token-file /run/secrets/ananta-user-token \
  dataset-register --request dataset.json \
  --idempotency-key dataset-import-20260822-01

python scripts/hrm_experiments_client.py \
  --base-url https://ananta.example \
  --token-file /run/secrets/ananta-user-token \
  run-start --request run.json \
  --idempotency-key experiment-20260822-01
```

Dataset registration, checkpoint admission, run creation, cancellation, and
evaluation bind the key to the canonical request. Reuse with another payload
is rejected; an accepted replay returns the durable original projection.

The shipped `hrm-sudoku-reference-v1`, `hrm-maze-reference-v1`, and
`hrm-arc-reference-v1` profiles are deterministic validation baselines, not
trained neural HRM checkpoints. A learned implementation must use the same
admitted-checkpoint and networkless-runner ports and receives no additional
orchestration authority.

Run the fail-closed static release check with:

```bash
python scripts/check_hrm_experiments_release_gate.py
```

The gate covers default-off behavior, Hub registration, Worker service scope,
non-orchestration constraints, runner isolation and limits, durable
idempotency, event high-water marks, closed contracts, explicit reference
profile naming, and forbidden runtime generation of `SRC_*` or `RUN_*` IDs.
