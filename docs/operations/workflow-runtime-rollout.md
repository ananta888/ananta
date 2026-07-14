# Workflow Runtime Rollout, Migration and Recovery Runbook

This runbook is the production procedure for Native, LangGraph and Temporal
workflow runtimes. It preserves Ananta's Hub–worker architecture: operators
change Hub selection policy; they do not send work directly to a worker or
Temporal Activity.

## Hard stop conditions

Do not enable, promote or roll back a production scope when any of these is
true:

- the workflow-runtime release gate is missing, failed, stale or has a contract
  hash different from the deployed plan/event/authorization contracts;
- a critical finding in the
  [threat-model gate](../security/workflow-runtime-security-gates.v1.json) is
  open, unmitigated or unverified;
- the target runtime lacks a required capability, including authorization,
  policy, audit, approval, side-effect guard, checkpoint, durability or resume;
- canonical projection is stale/inconsistent, a write is `uncertain`, or a
  completed run lacks verified artifact evidence;
- backup/restore, schema, replay or key-rotation drills have not passed for the
  candidate build;
- Hub, task queue, database or required runtime health is not ready.

`degraded`, `unsupported` and `incompatible` are not success states. An operator
approval cannot override a deterministic security or contract failure.

## Scope and policy precedence

Rollout is controlled independently by project, tenant, profile and workflow.
More-specific scopes may only narrow an inherited policy; they cannot add a
runtime, capability, fallback, egress destination or side-effect class denied
by a parent scope.

Every executable plan must contain a Hub-compiled `workflow_rollout_scope`.
Missing scope metadata is a hard denial, not a legacy-policy bypass. The Hub
always overwrites tenant and workflow values with the validated plan identities;
caller-supplied conflicting identities are rejected. A tenant administrator may
promote only tenant/profile/workflow scopes in that tenant. Project-wide changes
require the separate `system_admin` or `superadmin` authority.

| Scope | Typical use | Required audit fields |
| --- | --- | --- |
| project | global allowlist and initial mode | project, policy version, actor, evidence |
| tenant | data-locality, budget and tenant cohort | tenant-safe ID/hash, parent policy, reason |
| profile | durable/local/security expectations | profile version, required capabilities |
| workflow | canary and workflow-specific requirements | workflow ID, plan hash, selected/rejected runtimes |

Each effective policy contains:

```json
{
  "mode": "disabled | shadow | live | drain",
  "preferred_runtime": "native | langgraph | temporal",
  "allowed_runtimes": ["native", "langgraph", "temporal"],
  "required_capabilities": ["authorization", "audit", "side_effect_guard"],
  "allowed_side_effect_classes": ["none", "read"],
  "allowed_egress_destinations": [],
  "fallback_semantics": "none | equivalent-only"
}
```

The reviewed example is
[`rollout-policy.example.v1.json`](../../examples/workflow-runtime/rollout-policy.example.v1.json).
The Hub merges scopes deterministically, intersects allowlists, unions required
capabilities and records rejected alternatives. A missing safe candidate is
`blocked`, never an implicit `local` selection.

## Preflight and reproducible validation

From the repository root:

```bash
python scripts/validate_workflow_runtime_docs.py
python -m pytest -q \
  tests/test_workflow_runtime_reference_conformance.py \
  tests/test_workflow_runtime_safety.py \
  tests/test_native_graph_runtime.py \
  tests/test_workflow_lc_lg_live_langgraph.py \
  tests/test_temporal_runtime_contracts.py \
  tests/test_temporal_history_projection.py \
  tests/security/workflow_runtime/test_workflow_runtime_security_docs.py \
  tests/security/workflow_runtime/test_workflow_runtime_production_compose.py \
  tests/test_agent_token_file_auth.py

INITIAL_ADMIN_PASSWORD=compose-validation-only \
POSTGRES_PASSWORD=compose-validation-only \
TEMPORAL_POSTGRES_PASSWORD=compose-validation-only \
docker compose \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.temporal.yml \
  --profile temporal config --quiet

ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE=/absolute/deployment/path/workflow-auth-signing-keyring.json \
ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE=/absolute/deployment/path/workflow-auth-verification-keyring.json \
ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE=/absolute/deployment/path/workflow-dispatch-keyring.json \
ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-hub-service-token \
ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE=/absolute/deployment/path/workflow-hub-session-signing-key \
ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE=/absolute/deployment/path/workflow-worker-registration-keyring.json \
ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-worker-alpha-registration-token \
ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-worker-beta-registration-token \
ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-worker-alpha-service-token \
ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-worker-beta-service-token \
ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE=/absolute/deployment/path/workflow-worker-alpha-session-signing-key \
ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE=/absolute/deployment/path/workflow-worker-beta-session-signing-key \
ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_SECRET_FILE=/absolute/deployment/path/workflow-runtime-service-keyring.json \
ANANTA_WORKFLOW_TEMPORAL_SERVICE_TOKEN_SECRET_FILE=/absolute/deployment/path/workflow-temporal-service-token \
CORS_ORIGINS=https://ananta.example.invalid \
INITIAL_ADMIN_PASSWORD=compose-validation-only \
POSTGRES_PASSWORD=compose-validation-only \
TEMPORAL_POSTGRES_PASSWORD=compose-validation-only \
docker compose \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile temporal config --quiet
```

The values above are disposable validation values, not production credentials.
The second command contains paths only; `config` validates wiring, while the
auth and Compose contract tests validate fail-closed content handling and least
privilege consumers. Production secrets belong in mounted secret files or the
deployment secret manager and must not be committed or written to shell history.

For a real Temporal server/worker round trip, use an isolated project and tear
it down even after failure:

```bash
export TEMPORAL_POSTGRES_PASSWORD='set-in-a-private-shell'
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

The side-effect-free probe proves connectivity and registration only. It does
not replace the release gate, failure drills or a restore drill.

### Executable AIR-055 operations drill

The release-blocking local drill executes the repository's real Alembic graph
against an isolated SQLite database, performs `head -> N-1 -> head`, preserves
an N-1 workflow-budget row, creates and restores a consistent database backup,
reopens Hub rollout/audit/grant state, rotates and revokes a disposable
authorization key, and rehearses incident containment plus shadow-only
recovery. It also executes the Hub rollout services through `disabled ->
shadow -> live -> capability-safe rollback`: both a write and an egress intent
must be suppressed and audited in shadow, promotion attempts without an
approval reference or performance evidence must leave the policy unchanged,
and the final rollback target must preserve every plan, security, checkpoint
and resume capability. Run it from the repository root:

```bash
python scripts/run-workflow-runtime-operations-drills.py \
  --output /tmp/workflow-runtime-operations-evidence.json
```

The default workspace is temporary and removed after the run. Use
`--workspace /absolute/private/new-directory` only when an operator needs to
inspect the isolated databases; the directory must not already exist. The
report is content-addressed, contains no timestamps, paths, payloads,
signatures or key material, and is suitable as ephemeral CI evidence. A
missing drill, failed invariant, migration error or stale source revision is a
promotion blocker. This deterministic drill complements, but does not replace,
the release performance gate or the PostgreSQL/Temporal staging restore
described below. Its fixed performance measurements exercise the promotion
admission contract; only current measured release evidence may authorize a
real deployment. CI additionally runs
`tests/test_workflow_hub_task_gateway_runtime.py`, which loads the production
file-backed keyring and proves persisted key/envelope revocation; the drill
runner deliberately never writes production key files.

The runner reports plain `HEAD` only for a clean worktree. For a dirty tree it
uses `HEAD+dirty.sha256.<digest>`, where the digest covers the binary tracked
diff and every non-ignored untracked file. Passing plain `HEAD` explicitly for a
dirty tree is rejected. This makes local drill evidence honest without claiming
that uncommitted bytes belong to the checked-out commit. Deployment release
evidence must still be produced from the immutable candidate build.

### Optional OpenTelemetry export

Canonical events remain the source of truth; OTLP is a downstream, best-effort
projection and exporter failure must not change workflow state. Install the
optional packages only in images that export traces:

```bash
python -m pip install -e '.[observability]'
```

Configure the Hub/exporting process with:

| Variable | Rule |
| --- | --- |
| `ANANTA_WORKFLOW_OTEL_ENABLED` | opt-in boolean; absent/false keeps the canonical store undecorated |
| `ANANTA_WORKFLOW_OTEL_ENDPOINT` | required when enabled; HTTPS for remote collectors, HTTP only for localhost or internal `otel-collector` |
| `ANANTA_WORKFLOW_OTEL_HEADERS_FILE` | optional absolute path to a bounded JSON object of headers, mounted read-only as a secret |

Never place collector authorization headers directly in Compose YAML, `.env`,
the endpoint URL or a workflow plan. Use a TLS collector or the isolated local
Compose collector and mount the headers file only into the exporter container.
Validate file ownership/permissions and collector certificate before rollout.

Telemetry has fixed attribute keys, at most 32 attributes, bounded 256-character
link values and a 16 KiB payload limit. Oversized payload is replaced by digest
and byte count; arbitrary event payload keys are not labels. Alert on
`workflow_telemetry_export_failed`, queue saturation and collector rejection,
but recover missing spans from canonical events rather than treating telemetry
as state or replay evidence.

## Rollout phases

### 0. Disabled baseline

1. Set the candidate runtime to `disabled` at the project scope.
2. Capture current capability matrix, contract hash, profile versions, Hub and
   worker image digests, database revision and current Build IDs.
3. Confirm existing live runs and uncertain ledger operations. Never migrate an
   in-flight run by changing only its selector.
4. Take and validate backups as described below.

### 1. Side-effect-free shadow

Shadow receives a tenant-authorized copy of bounded inputs and produces a
separate evidence set. It has no influence on active routing, canonical active
status, approval state or user-visible artifacts.

Mandatory shadow controls:

- use the deterministic fake provider or a separately approved read-only local
  provider; default network egress is off;
- permit only `none` and explicitly safe `read` side-effect classes;
- replace write/tool invokers with `suppress_and_record_intent`; do not claim or
  complete an external side-effect ledger operation;
- do not send approval, signal, update or cancel commands to the active run;
- use separate shadow run/evidence correlation generated by the Hub and keep
  tenant binding; never copy secrets, raw provider credentials or artifact
  bodies;
- derive observations only from tenant/run-bound canonical Hub events. Both
  event sequences must be non-empty, contiguous, plan-hash-bound by their start
  event, cover every planned node and end in `workflow.run.completed`;
- compare only common capabilities and plan-derived deterministic invariants.
  Textual model differences may be reported but cannot waive security gates;
- sign `ananta.workflow_shadow_comparison.v2` with the Hub workflow keyring and
  publish it atomically as an owner-only (`0600`) regular file. The signature
  binds tenant, workflow, both run IDs, scope, plan hash, shadow-policy
  hash/version/revision, runtime versions/builds, capabilities, source revision
  and issue/expiry times. It is never itself production-eligible.

The expected shadow artifact records plan hash, runtime/build, capability set,
canonical event classes, intended operations, artifact schema/digests, cost,
latency, deviations and reason codes. A shadow result is never promoted merely
because its final text looks similar.

Abort shadow immediately on egress attempt, write invocation, cross-tenant
access, missing provenance, duplicate operation, unbounded retry, stale
projection or secret-like telemetry.

### 2. Canary by workflow and tenant

1. Keep project/profile in shadow; set one reviewed workflow in one non-critical
   tenant to `live` (or `durable` via the Temporal runtime mode).
2. Require a target runtime whose evidenced capabilities are a superset of the
   effective requirements.
3. Run failure, approval, cancel, crash and resume drills from the
   [example walkthrough](../examples/workflow-runtime/README.md).
4. Check the Hub operations view, not runtime-private UI alone:

   ```bash
   curl --fail --silent --show-error \
     --header "Authorization: Bearer ${ANANTA_ACCESS_TOKEN}" \
     'http://localhost:5000/api/workflow-runtime/operations?health=degraded'
   ```

   The same projection can be inspected without duplicating evaluation logic:

   ```bash
   ANANTA_AUTH_TOKEN="${ANANTA_ACCESS_TOKEN}" \
     ananta runtime operations --health degraded
   # In the Operator TUI: :ops runtime degraded
   ```

   Use a short-lived operator token in a private shell. Do not paste command
   output containing identifiers into public tickets.
5. Confirm zero unverified success, unexplained fallback, open gate, uncertain
   operation, cross-tenant denial anomaly or projection inconsistency.

### 3. Profile and project promotion

Expand one dimension at a time: workflow cohort, then tenant cohort, then
profile, then project. Preserve the previous policy as a signed/versioned
rollback artifact. At each step record actor, approval, contract hash, evidence,
selected/rejected runtime reason codes and observation window.

Promotion changes only new run selection. Existing runs stay pinned to their
runtime/build and complete or are explicitly cancelled/reconciled. Never
translate checkpoint formats implicitly.

### 4. Drain and retirement

Set the old runtime to `drain`: no new runs, queries/cancel/reconciliation still
available. Wait for active runs to become terminal and all operations to be
`completed`, `failed` or operator-reconciled. Retain history, key verification
material and the old worker build through the documented replay/retention
window. Then remove it from `allowed_runtimes`; deletion is a separate approved
change after restore evidence exists.

## Capability-safe rollback

Rollback routes new runs to a prior runtime; it does not convert runtime-private
state. Before rollback, evaluate the exact affected plan/profile:

1. Target release evidence is green and matches the deployed contract hash.
2. Target capabilities cover the full effective required set.
3. No protected capability is lost. `equivalent-only` fallback semantics are
   explicit; degraded/incompatible rollback is rejected.
4. Active runs remain on the source runtime, or are cancelled and reconciled.
5. All side-effect ledger records are known. `uncertain` operations block
   replay/restart until external evidence is reconciled.
6. Target database/schema/key versions can read the canonical state.

Rollback order:

1. Set affected workflow/tenant/profile to `drain`.
2. Disable new source-runtime selection.
3. Re-run capability/security/conformance gate for the target.
4. Set `preferred_runtime` to the target and `allowed_runtimes` to the reviewed
   set; keep fallback `none` until verification.
5. Start one side-effect-free canary, then one approved idempotent canary.
6. Verify canonical events, artifacts, ledger and UI evidence before expansion.

If no compatible target exists, leave new runs `blocked`. Availability pressure
does not justify loss of authorization, policy, audit, side-effect safety,
durability or resume.

## Backup and restore

Use `umask 077`, encrypted storage and a backup identity separate from the Hub.
Back up the Ananta database, Temporal database, artifact store, profile/policy
versions, release evidence and secret-manager key versions. Never put key values
inside the database dump or test artifact.

Example PostgreSQL backups from a running full+Temporal stack:

```bash
umask 077
mkdir -p backups/workflow-runtime
docker compose \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.temporal.yml \
  --profile temporal exec -T postgres \
  pg_dump --username "${POSTGRES_USER:-ananta}" \
  --dbname "${POSTGRES_DB:-ananta}" --format=custom \
  > backups/workflow-runtime/ananta.pgdump
docker compose \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.temporal.yml \
  --profile temporal exec -T temporal-db \
  pg_dump --username "${TEMPORAL_POSTGRES_USER:-temporal}" \
  --dbname "${TEMPORAL_POSTGRES_DB:-temporal}" --format=custom \
  > backups/workflow-runtime/temporal.pgdump
sha256sum backups/workflow-runtime/*.pgdump \
  > backups/workflow-runtime/SHA256SUMS
```

Validate format before accepting the backup:

```bash
docker compose \
  -f docker/compose-next/compose.stack.full.yml \
  run --rm --no-deps --entrypoint pg_restore postgres \
  --list < backups/workflow-runtime/ananta.pgdump > /dev/null
docker compose \
  -f docker/compose-next/compose.temporal.yml \
  --profile temporal run --rm --no-deps --entrypoint pg_restore temporal-db \
  --list < backups/workflow-runtime/temporal.pgdump > /dev/null
sha256sum --check backups/workflow-runtime/SHA256SUMS
```

Restore is always rehearsed into isolated databases/staging infrastructure, not
over a live database. Stop application writers, create empty restore targets,
restore with `--exit-on-error`, run `alembic current`, rebuild canonical read
models, replay stored Temporal N-1 histories, compare event/ledger/artifact
counts and execute the reference workflows. A successful `pg_restore` exit code
without application and replay checks is not restore evidence.

Record dump digest, source/target DB versions, schema revisions, image/Build IDs,
contract hash, row/count invariants and gate artifact. Destroy the drill target
only after review.

Before accepting environment-specific restore evidence, run the executable
baseline above and compare its schema revision and logical invariant set with
the staging restore. Never copy its disposable SQLite file into production.

## Schema migration

1. Put affected scopes in `drain`; stop new runs and wait/reconcile writes.
2. Take validated backups and record current Alembic and Temporal schema/build.
3. Verify the migration graph without changing production:

   ```bash
   docker compose -f docker/compose-next/compose.stack.full.yml \
     run --rm --no-deps \
     -e ANANTA_QUICKSTART_MODE=agent-only ai-agent-hub alembic heads
   docker compose -f docker/compose-next/compose.stack.full.yml \
     run --rm --no-deps \
     -e ANANTA_QUICKSTART_MODE=agent-only ai-agent-hub alembic current
   ```
4. Apply to an isolated restore, then `alembic upgrade head`; run projection
   rebuild, conformance, security and replay gates.
5. Apply one production migration owner at a time. Application containers must
   not race independent migration jobs.
6. Promote only after old readers/new writers and rollback compatibility stated
   by the migration have been tested.

Use official Temporal upgrade paths and compatibility tables; do not run Ananta
Alembic migrations against the Temporal database. Database rollback prefers
restore/forward-fix. Run `alembic downgrade` only when that exact downgrade was
implemented and rehearsed and no new-version data would be lost.

## Key rotation

Authorization HMAC and dispatch-encryption Fernet keyrings are separate. Do not
reuse either key as a Hub session secret, provider credential or Temporal API
key. Keyring files are absolute, read-only secret mounts.

Two-phase rotation:

1. Generate a new key in the secret manager and add it as verification/decrypt
   capable while the old key remains active.
2. Roll Hub and relevant runtime containers; verify every instance reports the
   expected key IDs/build and can read existing signed/encrypted records.
3. Make the new key active on the Hub first, then on command producers; issue a
   canary envelope/checkpoint/dispatch and verify it end to end.
4. Wait beyond maximum envelope/command TTL and the active-run/checkpoint
   compatibility window. Re-encrypt long-lived encrypted dispatch records if
   retention requires it.
5. Revoke/remove the old signing key only after no resumable state depends on
   it. Keep an offline recovery copy according to retention policy, with audited
   access.

Emergency compromise skips the normal wait: disable affected production
scopes, revoke the key/contract, isolate gateway and provider egress, rotate,
reconcile all operations signed during the exposure window, and require fresh
approvals. A revoked-key run is not silently resumed.

The Hub service token is rotated separately. Atomically replace its external
source file, then recreate the Hub and Temporal worker together; never use the
application `/rotate-token` path for a file-managed credential. Verify a strict
internal-gateway request with the new token and rejection of the old token
before reenabling workflow scopes.

## Incident response

Incident triggers include suspected key/credential exposure, cross-tenant data,
duplicate side effect, signature/replay/fence anomaly, poisoned state, history
gap/tamper, unexplained provider egress, unbounded retry, stale UI success or
loss of canonical evidence. A telemetry outage alone does not invalidate stored
canonical state, but unexpected exporter egress, leaked headers or unbounded
cardinality requires immediate exporter disablement and credential rotation.

1. **Contain:** set affected project/tenant/profile/workflow to `disabled` or
   `drain`; keep unaffected scopes explicit. Isolate egress and the internal
   gateway where required. Do not destroy containers/volumes yet.
2. **Preserve:** snapshot canonical events, ledger rows, ownership/checkpoint
   references, Temporal history references, audit and release evidence. Use
   digests and redacted exports; do not copy decrypted payloads or secrets.
3. **Revoke:** expire approvals and commands; revoke compromised signing,
   encryption, API or provider credentials through their owner.
4. **Reconcile:** inspect every stable operation in the exposure window against
   external artifact/effect evidence. Mark unknown as `uncertain`; do not retry.
5. **Recover:** restore or forward-fix in staging, rebuild projections, replay
   histories, run security/conformance/reference gates, then use the staged
   rollout phases.
6. **Close:** record root cause, tenant impact, contracts/builds, evidence and
   control/test change. A UI status edit is not incident closure.

## Release-blocking Compose performance gate

The reference profile is `docker-compose-temporal-reference-v1`. Its CI gate
collects at least ten real Temporal start and signal samples, at least ten Hub
canonical-event projection samples, and an end-to-end observation from hard
worker kill through durable resume. The gate recomputes nearest-rank P95 from
the raw samples; a reported summary cannot override those samples.

Promotion is blocked unless all comparisons are strictly below their limits:

| Metric | Required P95 |
| --- | ---: |
| workflow start | `< 2000 ms` |
| signed signal through completion | `< 2000 ms` |
| canonical event projection | `< 1000 ms` |
| resume after hard worker replacement | `< 30000 ms` |

The Compose job writes
`ci-artifacts/temporal-failure-gate/compose-performance-evidence.json` with
schema `ananta.workflow_runtime_compose_performance_evidence.v1`. It is
uploaded as ephemeral CI evidence; it is not a hand-authored repository
artifact. Every record binds the source revision, runtime, reference profile,
raw samples, recomputed P95, threshold, sample count and a content-derived
evidence ID. Production promotion consumes only a `passed` record with the
expected source revision. It additionally requires a content-addressed and
Hub-signed `ananta.workflow_shadow_comparison.v2` record for the same tenant,
workflow, rollout scope, target runtime version/build, plan, current shadow
policy revision and source revision. The file must be a regular file with mode
`0600`, and its signed expiry must still be current. Set the two absolute
read-only paths with `ANANTA_WORKFLOW_RUNTIME_PERFORMANCE_EVIDENCE_FILE` and
`ANANTA_WORKFLOW_RUNTIME_SHADOW_EVIDENCE_FILE`; missing, mismatched, tampered or
non-passing evidence fails closed before the policy can become `live`.
The promotion service revalidates the exact approval digest itself (tenant,
workflow, scope, target policy hash, plan hash, expected revision and change
ID); route-only approval checks are insufficient. The target live policy may
change only mode/version/evidence references from its evidenced shadow baseline.

To reproduce the non-network projection sampler and evaluate collected Compose
components:

```bash
python scripts/run-workflow-runtime-performance-gate.py \
  sample-projection --count 20 --output /tmp/performance-projection.json
python scripts/run-workflow-runtime-performance-gate.py evaluate \
  --component /tmp/performance-temporal.jsonl \
  --component /tmp/performance-projection.json \
  --component /tmp/performance-worker-restart.json \
  --source-revision "$(git rev-parse HEAD)" \
  --output /tmp/compose-performance-evidence.json
```

Do not synthesize missing metrics or copy evidence from another revision. A
missing, malformed, stale, threshold-equal or threshold-exceeding observation
is `blocked` and cannot be waived by an operator approval.

## Mandatory staging drill and evidence

At least once per release candidate, perform: disabled baseline → shadow →
workflow canary → tenant/profile promotion → compatible rollback → database and
Temporal restore → key rotation → incident containment. Exercise worker/Hub
crash, Temporal restart, hub-before/after-task failure, timeout uncertainty,
duplicate signal, cancellation race and stale history.

The drill passes only when canonical state rebuilds identically, no suppressed
shadow write occurred, no non-idempotent operation duplicated, all expected
artifacts verify, rollback retained capabilities, restored histories replay and
the UI reports evidence/gaps honestly. Store only stable structured evidence
under the repository's approved test-gate artifact path; live dumps, logs and
credentials are runtime data and are never committed.

See also the [Temporal-specific runbook](temporal-runtime.md),
[runtime-neutral example](../examples/workflow-runtime/README.md),
[architecture](../architecture/workflow-runtime.md) and
[threat model](../security/workflow-runtime-threat-model.md).
