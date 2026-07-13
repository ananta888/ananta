# Temporal Runtime Operations

## Architecture boundary

Temporal is optional durable infrastructure behind Ananta's hub control plane.
It is not an alternative control plane and does not own Ananta's task queue.
The dedicated Temporal worker registers workflows and Activities; an Activity
can only submit an authorization-bound command to the hub. The hub validates
policy and budget, records the side-effect-ledger decision and creates a normal
delegated task. No Temporal component selects or calls an Ananta worker.

The implementation protects SRP and DIP with separate contracts, Activity
gateway, history projector and worker shell. The worker verifies the neutral
authorization wire format without importing Hub services or persistence code.

## Install and configuration

Native-only installations do not import the Temporal SDK. For a development
installation use the exact optional extra:

```bash
python -m pip install -e '.[temporal]'
```

The dedicated container pins the same `temporalio==1.30.0` version. Relevant
worker settings are:

| Variable | Purpose |
| --- | --- |
| `ANANTA_TEMPORAL_ADDRESS` | Temporal frontend address |
| `ANANTA_TEMPORAL_NAMESPACE` | Namespace |
| `ANANTA_TEMPORAL_TASK_QUEUE` | Versioned worker task queue |
| `ANANTA_TEMPORAL_BUILD_ID` | Deployment/build identity |
| `ANANTA_TEMPORAL_IDENTITY` | Poller identity shown in Temporal |
| `ANANTA_TEMPORAL_TLS_*` | TLS server name and CA/client file references |
| `ANANTA_TEMPORAL_API_KEY_FILE` | API-key file reference |
| `ANANTA_TEMPORAL_HUB_IDENTITY` | Separate audited Temporal SDK identity for the Hub client |
| `ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE` | Hub-only Ed25519 private signing-keyring file |
| `ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE` | Worker-only Ed25519 public verification-keyring file |
| `ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE` | Hub-only dispatch-encryption keyring file |
| `ANANTA_TEMPORAL_HUB_URL` | Hub endpoint used only by the Activity gateway |
| `ANANTA_TEMPORAL_HUB_TOKEN_FILE` | Hub service-token file reference |
| `AGENT_TOKEN_FILE` | Hub-side source for the same service token; strict routes fail closed if invalid |

The two authorization keyring files are JSON and must be mounted read-only. The
Hub file contains private signing seeds:

```json
{
  "schema": "ananta.workflow-auth-signing-keyring.v1",
  "algorithm": "ed25519",
  "active_key_id": "runtime-2026-07",
  "private_keys": {
    "runtime-2026-07": "base64-encoded-32-byte-private-seed"
  }
}
```

Temporal receives a different file containing only the matching public key:

```json
{
  "schema": "ananta.workflow-auth-verification-keyring.v1",
  "algorithm": "ed25519",
  "public_keys": {
    "runtime-2026-07": "base64-encoded-32-byte-public-key"
  }
}
```

The Hub must never mount the Worker file as a substitute for its signer, and a
Worker must never mount the signing file. Verification loaders reject private
or legacy symmetric fields. Shared HMAC is disabled by default; its explicit
compatibility flag is for isolated development migration only.

The dispatch-encryption keyring has the same JSON envelope but every value must
be a URL-safe Fernet key. It is mounted into the Hub only. The Hub service-token
file contains one random token of at least 32 bytes and is mounted into the Hub
as `AGENT_TOKEN_FILE` and into the Temporal worker as
`ANANTA_TEMPORAL_HUB_TOKEN_FILE`. The file reader rejects relative paths,
oversized/short values, embedded whitespace, group/world-writable source files
and conflicting inline `AGENT_TOKEN` values. It also rejects URL query-token
authentication for file-managed credentials. Application token rotation is
disabled while the token is externally file-managed.

Do not place any of these files in Compose YAML, `.env`, images or repository
fixtures. Generate the four source files in a deployment-owned directory. The
following example is verifiable and writes only beneath `/etc/ananta/secrets`:

```bash
sudo install -d -m 0700 /etc/ananta/secrets
sudo python - <<'PY'
import base64
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

root = Path("/etc/ananta/secrets")
auth_id = "workflow-auth-v1"
dispatch_id = "workflow-dispatch-v1"
private = Ed25519PrivateKey.generate()
private_seed = private.private_bytes(
    serialization.Encoding.Raw,
    serialization.PrivateFormat.Raw,
    serialization.NoEncryption(),
)
public_key = private.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
files = {
    "workflow-auth-signing-keyring.json": json.dumps({
        "schema": "ananta.workflow-auth-signing-keyring.v1",
        "algorithm": "ed25519",
        "active_key_id": auth_id,
        "private_keys": {auth_id: base64.b64encode(private_seed).decode("ascii")},
    }),
    "workflow-auth-verification-keyring.json": json.dumps({
        "schema": "ananta.workflow-auth-verification-keyring.v1",
        "algorithm": "ed25519",
        "public_keys": {auth_id: base64.b64encode(public_key).decode("ascii")},
    }),
    "workflow-dispatch-keyring.json": json.dumps({
        "active_key_id": dispatch_id,
        "keys": {dispatch_id: base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")},
    }),
    "workflow-hub-service-token": base64.urlsafe_b64encode(os.urandom(48)).decode("ascii"),
}
for name, value in files.items():
    path = root / name
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
PY
```

Set only source-file *paths* in the deployment shell and validate the fully
merged model before startup:

```bash
export ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-auth-signing-keyring.json
export ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-auth-verification-keyring.json
export ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-dispatch-keyring.json
export ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-hub-service-token

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile temporal config --quiet
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile temporal up -d --build
```

The production overlay connects only the Hub to Temporal's internal network,
gives the dispatch key to the Hub only, and gives the authorization keyring and
service token only to the Hub and dedicated Temporal worker. The Angular
frontend and normal Ananta workers receive none of them. The base Temporal and
probe overlays remain credential-free; the probe therefore remains safe to run
without production secrets.

Rotate the active authorization key through the Hub first, distribute a
verification keyring containing old and new keys, wait until all workers report
the new Build ID, and only then stop issuing the previous key. Rotate the Hub
service token by atomic replacement of its external source file followed by a
controlled Hub/Temporal-worker recreate. A failed secret validation returns
`workflow_auth_configuration_invalid`; do not roll back to inline tokens.

## Health and graceful shutdown

- `GET /live` reports whether the worker process and health server are alive.
- `GET /ready` succeeds only after the SDK client and worker pollers were built.
- `GET /health` returns the combined redacted state.
- On SIGTERM/SIGINT readiness is removed first, the worker drains for
  `ANANTA_TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS`, then exits.

Readiness only proves poller registration. Execute
`AnantaTemporalProbeWorkflow` to prove an end-to-end server/task-queue/worker
roundtrip. The probe Activity performs no I/O and has no side effect.

## Retry and cancellation policy

Activities use four finite profiles: read-only, idempotent, non-idempotent and
long-running. Non-idempotent Activities have `maximum_attempts=1`; a later
attempt requires a new hub ledger decision. Long-running Activities heartbeat
only operation, task and checkpoint references. Prompts, results, credentials
and artifact contents are forbidden in heartbeat details.

Temporal retries are additionally bounded by the retry budget carried in the
signed envelope. The hub remains the authoritative combined retry-budget owner
for Temporal, hub-task, worker, tool and provider attempts. A timeout after a
possibly executed operation is `uncertain`, never an automatic success or a
blind retry.

## History projection and replay

The hub incrementally maps Temporal History Event IDs into canonical Ananta
events. Projection cursor, mapping version, consistency state and a safe
activity-to-step index are stored in PostgreSQL/SQLite. Raw payloads are not
copied; the read model contains only a tenant-protected `temporal://` reference.

The projector reports:

- `current` only after reaching the end of history;
- `stale` for unavailable history, page-budget exhaustion or mapping upgrade;
- `inconsistent` for gaps, binding conflicts or unknown event versions.

Never treat stale or inconsistent projections as current state. A mapping
upgrade requires a deterministic full rebuild and comparison before promotion.

The release gate runs the SDK time-skipping environment on every change and a
real `compose.temporal.yml` probe explicitly in integration CI. Stored N-1
histories must replay against the current worker. Continue-as-new is not enabled;
the workflow fails closed at configured state/history thresholds and emits
evidence for a separate reviewed extension.

Run the real server/UI/worker probe from the repository root with an isolated
Compose project. The command returns the smoke container's exit status and
tears the stack down afterwards:

```bash
export TEMPORAL_POSTGRES_PASSWORD='replace-with-a-random-test-password'
docker compose --project-name ananta-temporal-gate \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.tests.temporal.yml \
  --profile temporal --profile temporal-test \
  up --build --abort-on-container-exit --exit-code-from temporal-smoke
docker compose --project-name ananta-temporal-gate \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.tests.temporal.yml \
  --profile temporal --profile temporal-test down --volumes
```

## Failure drills

The release-blocking scenario catalog is
`tests/workflow_runtime/temporal/failure_scenarios.v1.json`. Its contract test
requires at least ten unique scenarios and currently binds thirteen named
drills to executable evidence:

1. Worker process loss and history reconstruction.
2. Hub failure after an idempotent task was accepted.
3. Temporal server restart with a workflow in flight.
4. Heartbeat loss and bounded Activity retry.
5. Activity start-to-close timeout.
6. Cancel propagation while an Activity is unresolved.
7. Bounded retry and non-idempotent retry suppression.
8. Direct signal/cancel race, repeated ten times per CI run.
9. N-1 history replay against the candidate workflow.
10. Lost acknowledgement after a non-idempotent external effect.
11. History threshold exhaustion.
12. State threshold exhaustion.
13. Expired authorization and operation-binding mismatch.

The SDK gate uses Temporal's real time-skipping Test Environment, real Workers,
workflow histories and the SDK Replayer. The Compose gate additionally starts a
real PostgreSQL-backed Temporal server and worker. It holds a side-effect-free
probe open, restarts the Temporal server, then proves the same Workflow ID, Run
ID and request binding can complete. A second probe receives a hard `SIGKILL`
of the worker container and must complete after a replacement worker replays
its history. A fresh task-queue probe then verifies normal operation.

CI publishes `test-environment.xml`, `scenario-manifest.json`,
`test-environment-summary.json`, the raw before/after JSONL records,
`server-restart-evidence.json`, `worker-crash-evidence.json` and a schema-stable
Compose summary. The artifact contains identifiers and statuses only; it has no
credentials, payloads or timestamps. N-1 replay and all critical race
repetitions run in the same mandatory job, so a skip or missing scenario fails
the manifest contract.

`limit_evidence` reports the deterministic history-event estimate, serialized
state-byte measurement, configured thresholds and fail-closed decision. When a
threshold is crossed, `continue_as_new_required` is true but automatic
continue-as-new remains disabled. This is deliberate: state is not silently
split or discarded before a separately reviewed carry-forward contract exists.

The in-process worker-replacement test uses a controlled SDK worker shutdown so
the embedded Test Environment server remains valid; the Compose drill provides
the ungraceful `SIGKILL` evidence. The Hub-after-acceptance drill faults the real
Temporal Activity boundary with a deterministic Hub gateway, rather than
killing a production Hub process. A full Hub-container crash remains a staging
operations drill because it needs deployment credentials and the complete hub
stack; it is not represented as stronger evidence by a mock.

The production gate fails if confirmed canonical state disappears, an
unapproved side effect is duplicated, a non-idempotent uncertain effect is
reported as completed, projection consistency is not `current`, replay fails,
or a history/state threshold does not stop the workflow fail closed.

Before a Temporal production promotion, also run the common Hub-state
operations drill:

```bash
python scripts/run-workflow-runtime-operations-drills.py \
  --output /tmp/workflow-runtime-operations-evidence.json
```

It proves the Ananta Alembic N-1 cycle, Hub database backup/restore,
authorization-key overlap/revocation, audited incident containment, shadow
write/egress suppression, approval/evidence-gated promotion and a
capability-safe rollback. The local drill uses deterministic contract evidence;
when the worktree is dirty its source identity includes a SHA-256 digest of the
tracked diff and non-ignored untracked files rather than claiming plain `HEAD`;
it does not migrate the Temporal database and does not replace the real
Temporal server/worker restart, measured performance, PostgreSQL backup or
stored-history replay gates above.
