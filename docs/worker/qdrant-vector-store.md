# Qdrant vector store

Ananta supports [Qdrant](https://qdrant.tech/) as an optional worker-side VectorStore. JSON is the default; the Hub owns policy/tasks and workers only execute delegated work.

The tested pair is Qdrant Server `1.18.3` with `qdrant-client==1.18.0`, pinned to image `qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286`.

Runtime code uses `query_points`. The Python client is an optional dependency:

```bash
python -m pip install -e '.[qdrant]'
```

The standard installation and JSON tests work without it. See the
[availability contract](qdrant-vector-store-availability.md) for fallback behavior.

## Configuration and endpoint policy

Production-parsed examples:

- `config/examples/vector-store.json-local.json`
- `config/examples/vector-store.qdrant-local.json`
- `config/examples/vector-store.qdrant-remote.json`

The local example is REST-only and opt-in. The remote example intentionally
keeps JSON active until an authorized Hub override changes the provider.
Endpoint origins are exact scheme/host/port tuples. A remote origin requires
TLS, certificate verification and `external_calls_allowed=true`. Plain HTTP
is permitted only for loopback development outside this Compose profile.
Every non-loopback origin—including `qdrant` on the private Compose
network—must use HTTPS or gRPCS. `trusted_private_origins` can waive only the
external-call approval; it can never waive TLS or certificate verification.

Secrets are references, never values. Host processes normally use
`env://ANANTA_QDRANT_API_KEY`; containers use
`secretfile:///run/secrets/qdrant-api-key`. Do not put API keys in JSON,
Compose environment values, URLs, logs or task payloads.
Private-CA deployments use bounded `tls_ca_cert_ref`; containers resolve
`secretfile:///run/secrets/qdrant-tls-ca.pem` without changing process-wide
trust. Provision and rotate it according to the
[Qdrant TLS runbook](qdrant-vector-store-tls.md).
The Qdrant container drops all capabilities and adds only `DAC_READ_SEARCH`
to read Compose-mounted API-key and TLS files. Startup fails closed when any
required file is missing, unreadable or empty.

REST is the default transport. Host gRPC is not published by the default
profile. To opt in, add the explicit host overlay and add the exact
`grpcs://localhost:6334` origin to the Hub-owned endpoint policy:

```bash
docker compose \
  -f docker/compose-next/compose.qdrant.yml \
  -f docker/compose-next/compose.qdrant-grpc-host.yml \
  --profile qdrant config --quiet
```

Never enable `prefer_grpc` without an explicitly configured and allowlisted
gRPC origin.

## Start, readiness and stop

The TLS runbook includes a standalone Compose render check which does not
create secret files. Provision its three TLS files before starting Qdrant.
Create the API key locally if it does not exist:

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export ANANTA_QDRANT_TLS_CERT_FILE="$PWD/config/secrets/qdrant-tls-cert.pem"
export ANANTA_QDRANT_TLS_KEY_FILE="$PWD/config/secrets/qdrant-tls-key.pem"
install -d -m 0700 "$(dirname "$ANANTA_QDRANT_API_KEY_FILE")"
if test ! -s "$ANANTA_QDRANT_API_KEY_FILE"; then
  umask 077
  openssl rand -hex 32 > "$ANANTA_QDRANT_API_KEY_FILE"
fi
chmod 0600 "$ANANTA_QDRANT_API_KEY_FILE"
for required_file in \
  "$ANANTA_QDRANT_TLS_CA_FILE" \
  "$ANANTA_QDRANT_TLS_CERT_FILE" \
  "$ANANTA_QDRANT_TLS_KEY_FILE"; do
  test -s "$required_file"
done
openssl verify \
  -CAfile "$ANANTA_QDRANT_TLS_CA_FILE" \
  "$ANANTA_QDRANT_TLS_CERT_FILE"
docker compose \
  -f docker/compose-next/compose.qdrant.yml \
  --profile qdrant up -d --pull always --wait --wait-timeout 120 qdrant
```

For the complete quickstart, first provision the
[Worker identity and dispatch keys](qdrant-vector-worker-identity.md), then
include the identity, Qdrant and worker overlays. Run from the repository root
with a `.env` containing a non-empty `INITIAL_ADMIN_PASSWORD`; the mandatory render fails before container creation when that value is absent or empty:

```bash
set -euo pipefail
test -s .env
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export ANANTA_QDRANT_TLS_CERT_FILE="$PWD/config/secrets/qdrant-tls-cert.pem"
export ANANTA_QDRANT_TLS_KEY_FILE="$PWD/config/secrets/qdrant-tls-key.pem"
qdrant_stack=(docker compose --env-file .env \
  -f docker/compose-next/compose.stack.quickstart.yml \
  -f docker/compose-next/compose.workflow-runtime.dev-auth.yml \
  -f docker/compose-next/compose.qdrant.yml \
  -f docker/compose-next/compose.qdrant-workers.yml)
"${qdrant_stack[@]}" --profile qdrant config --quiet
"${qdrant_stack[@]}" --profile qdrant \
  up -d --build --wait --wait-timeout 180
```

Verify both container readiness and an authenticated data-plane request:

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
QDRANT_API_KEY="$(tr -d '\r\n' < "$ANANTA_QDRANT_API_KEY_FILE")"
test -n "$QDRANT_API_KEY"
docker compose \
  -f docker/compose-next/compose.qdrant.yml \
  --profile qdrant ps --status running qdrant
curl --fail --silent --show-error \
  --cacert "$ANANTA_QDRANT_TLS_CA_FILE" \
  https://localhost:6333/healthz >/dev/null
curl --fail --silent --show-error \
  --cacert "$ANANTA_QDRANT_TLS_CA_FILE" \
  -H "api-key: $QDRANT_API_KEY" \
  https://localhost:6333/collections >/dev/null
if curl --fail --silent \
  http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
  echo "plaintext Qdrant HTTP unexpectedly succeeded" >&2
  exit 1
fi
unset QDRANT_API_KEY
```

Stop and remove the service container while retaining both named volumes:

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
docker compose \
  -f docker/compose-next/compose.qdrant.yml \
  --profile qdrant down --remove-orphans
```

Compose project prefixes can change physical volume names. When
`COMPOSE_PROJECT_NAME` is set, inspect the names from
`docker compose ... config --volumes` rather than guessing them. Never add
`--volumes` to a production stop command.

## Rollout ownership

Effective configuration precedence is:

1. global JSON default;
2. explicit profile override;
3. explicit workspace override.

Only the Hub validates and persists overrides. Workers receive an immutable
resolved configuration in a delegated task. Endpoint allowlists, external-call
permission, TLS verification and secret references are immutable security
policy and cannot be widened by an override.

Example workspace opt-in:

```bash
set -euo pipefail
: "${HUB_URL:?set HUB_URL, for example https://ananta.example.test}"
: "${HUB_TOKEN:?set a short-lived admin bearer token}"
export WORKSPACE_ID=workspace-a
curl --fail --silent --show-error \
  -X PUT "$HUB_URL/api/vector-store/workspaces/$WORKSPACE_ID/override" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"domain":"codecompass","override":{"provider":"qdrant"},"expected_revision":0}'
```

Profile overrides require a global administrator. Workspace overrides require
an administrator authorized for that workspace.

### Explicit Hub-as-worker read capability

Index mutations always remain Hub-owned worker tasks. Synchronous
CodeCompass and Wiki retrieval use local JSON by default. A Qdrant workspace
override does not grant the Hub network or secret access. Use the isolated,
domain-specific start commands in the
[Hub Qdrant read-capability runbook](qdrant-vector-store-hub-read.md).
Each overlay grants one explicit read scope; mutations still enter the
Hub-owned task queue and execute on a delegated worker.

## Hub-owned index tasks

The mutation API accepts `index`, `refresh`, `rebuild`, `delete` and `migrate`.
Search is read-only and cannot enqueue a task. A task always includes trusted
workspace, repository, profile and domain values. The same scope is serialized
by the Hub. See the [vector-index taskflow](qdrant-vector-index-taskflow.md)
for Worker-side embedding and the digest-bound artifact transport.

The following helper polls a submitted task without exposing its bearer token:

```bash
wait_vector_task() {
  task_id="$1"
  output_file="$2"
  attempt=0
  while test "$attempt" -lt 120; do
    curl --fail --silent --show-error \
      -H "Authorization: Bearer $HUB_TOKEN" \
      "$HUB_URL/api/vector-store/index-tasks/$task_id" \
      > "$output_file"
    task_status="$(python3 - "$output_file" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["task"]["status"])
PY
)"
    case "$task_status" in
      completed|failed|cancelled) return 0 ;;
      queued|running) ;;
      *) echo "unexpected vector task status: $task_status" >&2; return 1 ;;
    esac
    attempt=$((attempt + 1))
    sleep 2
  done
  echo "vector task polling timed out" >&2
  return 1
}
```

### Scope-bound reset

A shared environment must never delete collections by prefix or remove the
whole data volume. Submit a `delete` task for one exact trusted scope:

```bash
set -euo pipefail
: "${HUB_URL:?set HUB_URL}"
: "${HUB_TOKEN:?set HUB_TOKEN}"
export WORKSPACE_ID=workspace-a
export REPOSITORY_ID=repository-a
export PROFILE_NAME=default
export VECTOR_DOMAIN=codecompass
export RESET_IDEMPOTENCY_KEY="scope-reset-$(date -u +%Y%m%dT%H%M%SZ)"
RESET_BODY="$(python3 - <<'PY'
import json
import os

print(json.dumps({
    "operation": "delete",
    "workspace_id": os.environ["WORKSPACE_ID"],
    "repository_id": os.environ["REPOSITORY_ID"],
    "profile_name": os.environ["PROFILE_NAME"],
    "domain": os.environ["VECTOR_DOMAIN"],
    "idempotency_key": os.environ["RESET_IDEMPOTENCY_KEY"],
    "priority": "high",
    "payload": {"delete_all_scope": True},
}, separators=(",", ":")))
PY
)"
RESET_RESPONSE="$(mktemp)"
RESET_STATUS="$(mktemp)"
trap 'rm -f "$RESET_RESPONSE" "$RESET_STATUS"' EXIT
curl --fail --silent --show-error \
  -X POST "$HUB_URL/api/vector-store/index-tasks" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$RESET_BODY" > "$RESET_RESPONSE"
RESET_JOB_ID="$(python3 - "$RESET_RESPONSE" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["task"]["job_id"])
PY
)"
wait_vector_task "$RESET_JOB_ID" "$RESET_STATUS"
python3 - "$RESET_STATUS" <<'PY'
import json
import sys

task = json.load(open(sys.argv[1], encoding="utf-8"))["task"]
if task["status"] != "completed":
    raise SystemExit(
        f"scope reset failed: {task['status']} "
        f"{(task.get('result') or {}).get('reason_code', '')}"
    )
PY
```

Deleting `qdrant-data` is allowed only for a disposable, isolated local
Compose project after resolving the exact project-scoped volume name.

## JSON-to-Qdrant migration, resume and JSON rollback

Migration is a Hub-owned taskflow. The worker-side migrator neither creates
tasks nor embeddings. It validates the existing JSON vectors, writes bounded
batches into an inactive versioned collection and activates only through one
alias swap. The source file is never deleted.

The input reference is relative to the Hub-owned publisher root
`/var/lib/ananta/vector-index-inputs` and carries a SHA-256 digest plus the
full canonical scope fingerprint. Its only accepted path is
`<domain>/<scope-fingerprint>/<sha256>.json`. The Qdrant worker overlay mounts
the same named volume read-only at the identical path on every eligible
worker. Publish the immutable source once through the Hub publisher; do not
copy separate mutable files into individual workers and never use the
forbidden legacy `migration.source_path` field:

```bash
set -euo pipefail
export WORKSPACE_ID=workspace-a
export REPOSITORY_ID=repository-a
export PROFILE_NAME=default
export VECTOR_DOMAIN=codecompass
export MIGRATION_SOURCE="$PWD/.rag/codecompass/vector_index.json"
test -f "$MIGRATION_SOURCE"
export MIGRATION_SOURCE_SHA256="$(sha256sum "$MIGRATION_SOURCE" | awk '{print $1}')"
export VECTOR_INDEX_INPUT_ROOT=/var/lib/ananta/vector-index-inputs
export MIGRATION_STAGING_NAME="ananta-vector-index-$MIGRATION_SOURCE_SHA256.json"
test -s .env
qdrant_stack=(docker compose --env-file .env \
  -f docker/compose-next/compose.stack.quickstart.yml \
  -f docker/compose-next/compose.workflow-runtime.dev-auth.yml \
  -f docker/compose-next/compose.qdrant.yml \
  -f docker/compose-next/compose.qdrant-workers.yml)
"${qdrant_stack[@]}" --profile qdrant config --quiet
"${qdrant_stack[@]}" --profile qdrant cp \
  "$MIGRATION_SOURCE" \
  "ai-agent-hub:/tmp/$MIGRATION_STAGING_NAME"
export MIGRATION_INPUT_REF="$(
  "${qdrant_stack[@]}" --profile qdrant exec -T \
    -e MIGRATION_STAGING_NAME \
    -e MIGRATION_SOURCE_SHA256 \
    -e WORKSPACE_ID \
    -e REPOSITORY_ID \
    -e PROFILE_NAME \
    -e VECTOR_DOMAIN \
    ai-agent-hub python - <<'PY'
import json, os
from pathlib import Path
from agent.services.vector_index_input_artifact_service import FilesystemVectorIndexInputPublisher
from worker.retrieval.vector_store_contract import VectorScope
source = Path("/tmp") / os.environ["MIGRATION_STAGING_NAME"]
try:
    reference = FilesystemVectorIndexInputPublisher(
        publish_root=os.environ["ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT"]
    ).publish(
        scope=VectorScope(os.environ["WORKSPACE_ID"], os.environ["REPOSITORY_ID"], os.environ["PROFILE_NAME"], os.environ["VECTOR_DOMAIN"]),
        content=source.read_bytes(),
        content_sha256=os.environ["MIGRATION_SOURCE_SHA256"],
    )
finally:
    source.unlink(missing_ok=True)
print(json.dumps(reference, separators=(",", ":")))
PY
)"
python3 -c 'import json,os; assert set(json.loads(os.environ["MIGRATION_INPUT_REF"])) == {"path","sha256","scope_fingerprint"}'
```

Set the compatibility contract to the state expected by the active embedding
configuration:

```bash
export VECTOR_DIMENSIONS=384
export VECTOR_DISTANCE=cosine
export EMBEDDING_PROVIDER=ollama
export EMBEDDING_MODEL=nomic-embed-text
export EMBEDDING_PROFILE=default
export VECTOR_ENCODING=float32
export EMBEDDING_CONFIG_HASH=replace-with-reviewed-config-hash
export VECTOR_SCHEMA_VERSION=vector_store.v1
export VECTOR_MANIFEST_HASH=replace-with-reviewed-manifest-hash
```

Submit a mandatory dry-run:

```bash
set -euo pipefail
: "${HUB_URL:?set HUB_URL}"
: "${HUB_TOKEN:?set HUB_TOKEN}"
export MIGRATION_IDEMPOTENCY_KEY="migration-dry-run-$(date -u +%Y%m%dT%H%M%SZ)"
MIGRATION_DRY_RUN_BODY="$(python3 - <<'PY'
import json
import os

compatibility = {
    "dimensions": int(os.environ["VECTOR_DIMENSIONS"]),
    "distance": os.environ["VECTOR_DISTANCE"],
    "provider": os.environ["EMBEDDING_PROVIDER"],
    "model": os.environ["EMBEDDING_MODEL"],
    "profile": os.environ["EMBEDDING_PROFILE"],
    "encoding": os.environ["VECTOR_ENCODING"],
    "config_hash": os.environ["EMBEDDING_CONFIG_HASH"],
    "schema_version": os.environ["VECTOR_SCHEMA_VERSION"],
    "manifest_hash": os.environ["VECTOR_MANIFEST_HASH"],
}
print(json.dumps({
    "operation": "migrate",
    "workspace_id": os.environ["WORKSPACE_ID"],
    "repository_id": os.environ["REPOSITORY_ID"],
    "profile_name": os.environ["PROFILE_NAME"],
    "domain": os.environ["VECTOR_DOMAIN"],
    "idempotency_key": os.environ["MIGRATION_IDEMPOTENCY_KEY"],
    "payload": {
        "input_ref": json.loads(os.environ["MIGRATION_INPUT_REF"]),
        "compatibility": compatibility,
        "migration": {"dry_run": True},
        "batch_size": 128,
    },
}, separators=(",", ":")))
PY
)"
DRY_RUN_RESPONSE="$(mktemp)"
DRY_RUN_STATUS="$(mktemp)"
trap 'rm -f "$DRY_RUN_RESPONSE" "$DRY_RUN_STATUS"' EXIT
curl --fail --silent --show-error \
  -X POST "$HUB_URL/api/vector-store/index-tasks" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$MIGRATION_DRY_RUN_BODY" > "$DRY_RUN_RESPONSE"
DRY_RUN_JOB_ID="$(python3 - "$DRY_RUN_RESPONSE" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["task"]["job_id"])
PY
)"
wait_vector_task "$DRY_RUN_JOB_ID" "$DRY_RUN_STATUS"
python3 - "$DRY_RUN_STATUS" <<'PY'
import json
import sys

task = json.load(open(sys.argv[1], encoding="utf-8"))["task"]
reason = (task.get("result") or {}).get("reason_code")
if task["status"] != "completed" or reason != "migration_ready":
    raise SystemExit(f"migration dry-run did not pass: {task['status']} {reason}")
PY
```

After review, submit the real migration with a new idempotency key. Setting
`max_batches=1` deliberately exercises checkpoint/resume; omit it for an
uninterrupted run:

```bash
set -euo pipefail
export MIGRATION_IDEMPOTENCY_KEY="migration-run-$(date -u +%Y%m%dT%H%M%SZ)"
MIGRATION_BODY="$(python3 - <<'PY'
import json
import os

compatibility = {
    "dimensions": int(os.environ["VECTOR_DIMENSIONS"]),
    "distance": os.environ["VECTOR_DISTANCE"],
    "provider": os.environ["EMBEDDING_PROVIDER"],
    "model": os.environ["EMBEDDING_MODEL"],
    "profile": os.environ["EMBEDDING_PROFILE"],
    "encoding": os.environ["VECTOR_ENCODING"],
    "config_hash": os.environ["EMBEDDING_CONFIG_HASH"],
    "schema_version": os.environ["VECTOR_SCHEMA_VERSION"],
    "manifest_hash": os.environ["VECTOR_MANIFEST_HASH"],
}
print(json.dumps({
    "operation": "migrate",
    "workspace_id": os.environ["WORKSPACE_ID"],
    "repository_id": os.environ["REPOSITORY_ID"],
    "profile_name": os.environ["PROFILE_NAME"],
    "domain": os.environ["VECTOR_DOMAIN"],
    "idempotency_key": os.environ["MIGRATION_IDEMPOTENCY_KEY"],
    "payload": {
        "input_ref": json.loads(os.environ["MIGRATION_INPUT_REF"]),
        "compatibility": compatibility,
        "migration": {"dry_run": False, "max_batches": 1},
        "batch_size": 128,
    },
}, separators=(",", ":")))
PY
)"
MIGRATION_RESPONSE="$(mktemp)"
MIGRATION_STATUS="$(mktemp)"
trap 'rm -f "$MIGRATION_RESPONSE" "$MIGRATION_STATUS"' EXIT
curl --fail --silent --show-error \
  -X POST "$HUB_URL/api/vector-store/index-tasks" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$MIGRATION_BODY" > "$MIGRATION_RESPONSE"
MIGRATION_JOB_ID="$(python3 - "$MIGRATION_RESPONSE" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["task"]["job_id"])
PY
)"
while true; do
  wait_vector_task "$MIGRATION_JOB_ID" "$MIGRATION_STATUS"
  read -r task_status reason_code <<EOF
$(python3 - "$MIGRATION_STATUS" <<'PY'
import json
import sys

task = json.load(open(sys.argv[1], encoding="utf-8"))["task"]
print(task["status"], (task.get("result") or {}).get("reason_code", ""))
PY
)
EOF
  if test "$task_status" = completed; then
    test "$reason_code" = migrated
    break
  fi
  if test "$task_status" != failed || test "$reason_code" != migration_paused; then
    echo "migration failed: $task_status $reason_code" >&2
    exit 1
  fi
  curl --fail --silent --show-error \
    -X POST \
    -H "Authorization: Bearer $HUB_TOKEN" \
    "$HUB_URL/api/vector-store/index-tasks/$MIGRATION_JOB_ID/retry" \
    >/dev/null
done
```

The Hub reuses the same task idempotency key and binds the returned checkpoint
to the same trusted scope. Never copy a checkpoint into another scope.

Rollback to JSON is a control-plane change, not a code change and not a
collection deletion. First verify that the workspace's JSON state is compatible
with the expected embedding contract. Then remove the exact workspace override
using its current revision:

```bash
set -euo pipefail
: "${QDRANT_OVERRIDE_REVISION:?set the current workspace override revision}"
ROLLBACK_RESPONSE="$(mktemp)"
RESOLVED_RESPONSE="$(mktemp)"
trap 'rm -f "$ROLLBACK_RESPONSE" "$RESOLVED_RESPONSE"' EXIT
curl --fail --silent --show-error \
  -X DELETE "$HUB_URL/api/vector-store/workspaces/$WORKSPACE_ID/override" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "{\"domain\":\"$VECTOR_DOMAIN\",\"expected_revision\":$QDRANT_OVERRIDE_REVISION}" \
  > "$ROLLBACK_RESPONSE"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $HUB_TOKEN" \
  "$HUB_URL/api/vector-store/resolved-config?workspace_id=$WORKSPACE_ID&profile_name=$PROFILE_NAME&domain=$VECTOR_DOMAIN" \
  > "$RESOLVED_RESPONSE"
python3 - "$RESOLVED_RESPONSE" <<'PY'
import json
import sys

resolved = json.load(open(sys.argv[1], encoding="utf-8"))["resolved_config"]
if resolved["provider"] != "json":
    raise SystemExit("rollback did not resolve to JSON")
PY
```

Complete the gate with a read-only application search. Its trace must report
`requested_provider=json`, `effective_provider=json`, the exact trusted scope
and no `provider_fallback`. An incompatible JSON state is a failed rollback
gate (`fallback_state_incompatible`), not an empty successful result. The
former Qdrant collection remains available for an explicit forward rollback.

## Snapshot backup

Collection snapshots do not contain aliases, so the alias map is separate
mandatory evidence. Snapshot files and metadata below are downloaded outside
both the Qdrant data and snapshot volumes.

Set the exact active alias and collection after reviewing `/aliases`:

```bash
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export CURL_CA_BUNDLE="$ANANTA_QDRANT_TLS_CA_FILE"
export QDRANT_URL=https://localhost:6333
export QDRANT_ALIAS=ananta-codecompass-replace-with-reviewed-scope-digest
export QDRANT_COLLECTION=ananta-codecompass-replace-with-exact-version
export QDRANT_BACKUP_ROOT="$PWD/backups/qdrant/$(date -u +%Y%m%dT%H%M%SZ)"
```

Create and download one authenticated snapshot:

```bash
set -euo pipefail
umask 077
install -d -m 0700 "$QDRANT_BACKUP_ROOT"
QDRANT_API_KEY="$(tr -d '\r\n' < "$ANANTA_QDRANT_API_KEY_FILE")"
test -n "$QDRANT_API_KEY"
curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/aliases" \
  > "$QDRANT_BACKUP_ROOT/aliases.json"
python3 - "$QDRANT_BACKUP_ROOT/aliases.json" <<'PY'
import json
import os
import sys

aliases = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["aliases"]
matches = [
    item for item in aliases
    if item["alias_name"] == os.environ["QDRANT_ALIAS"]
]
if len(matches) != 1:
    raise SystemExit("reviewed alias is not present exactly once")
if matches[0]["collection_name"] != os.environ["QDRANT_COLLECTION"]:
    raise SystemExit("reviewed alias does not resolve to reviewed collection")
PY
curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION" \
  > "$QDRANT_BACKUP_ROOT/collection-info.json"
curl --fail --silent --show-error \
  -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"filter":{"must":[{"key":"_ananta_record_type","match":{"value":"manifest"}}]},"limit":1,"with_payload":true,"with_vector":false}' \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/points/scroll" \
  > "$QDRANT_BACKUP_ROOT/manifest.json"
python3 - "$QDRANT_BACKUP_ROOT/manifest.json" <<'PY'
import json
import sys

points = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["points"]
if len(points) != 1:
    raise SystemExit("collection does not contain exactly one Ananta manifest")
payload = points[0].get("payload", {})
if payload.get("_ananta_record_type") != "manifest":
    raise SystemExit("Ananta manifest payload is missing")
PY
curl --fail --silent --show-error \
  -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots" \
  > "$QDRANT_BACKUP_ROOT/snapshot-create.json"
SNAPSHOT_NAME="$(python3 - "$QDRANT_BACKUP_ROOT/snapshot-create.json" <<'PY'
import json
import sys

name = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["name"]
if not name or "/" in name or name in {".", ".."}:
    raise SystemExit("invalid snapshot name returned by Qdrant")
print(name)
PY
)"
curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/$SNAPSHOT_NAME" \
  --output "$QDRANT_BACKUP_ROOT/$SNAPSHOT_NAME"
printf '%s\n' "$QDRANT_ALIAS" > "$QDRANT_BACKUP_ROOT/alias-name.txt"
printf '%s\n' "$QDRANT_COLLECTION" > "$QDRANT_BACKUP_ROOT/collection-name.txt"
sha256sum "$QDRANT_BACKUP_ROOT/$SNAPSHOT_NAME" \
  > "$QDRANT_BACKUP_ROOT/$SNAPSHOT_NAME.sha256"
chmod 0600 "$QDRANT_BACKUP_ROOT"/*
unset QDRANT_API_KEY
```

Copy the whole backup directory, not only the snapshot, to protected storage.
Qdrant requires snapshot compatibility with the server version; keep the
recorded server version and restore with the same tested minor version.

## Snapshot restore

Restore into an isolated recovery environment first. The target collection
must be absent. Use the exact collection name recorded at backup time so the
deterministic Ananta manifest point remains addressable. Do not overwrite a
live collection.

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export CURL_CA_BUNDLE="$ANANTA_QDRANT_TLS_CA_FILE"
export QDRANT_URL=https://localhost:6333
: "${QDRANT_BACKUP_ROOT:?set QDRANT_BACKUP_ROOT}"
export QDRANT_ALIAS="$(tr -d '\r\n' < "$QDRANT_BACKUP_ROOT/alias-name.txt")"
export QDRANT_COLLECTION="$(tr -d '\r\n' < "$QDRANT_BACKUP_ROOT/collection-name.txt")"
SNAPSHOT_FILE="$(find "$QDRANT_BACKUP_ROOT" -maxdepth 1 -type f -name '*.snapshot' -print -quit)"
test -n "$SNAPSHOT_FILE"
(cd "$QDRANT_BACKUP_ROOT" && sha256sum --check "$(basename "$SNAPSHOT_FILE").sha256")
QDRANT_API_KEY="$(tr -d '\r\n' < "$ANANTA_QDRANT_API_KEY_FILE")"
test -n "$QDRANT_API_KEY"
EXISTING_STATUS="$(curl --silent --show-error \
  -o "$QDRANT_BACKUP_ROOT/restore-preflight.json" \
  -w '%{http_code}' \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION")"
test "$EXISTING_STATUS" = 404
curl --fail --silent --show-error \
  -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  -F "snapshot=@$SNAPSHOT_FILE" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/upload?wait=true&priority=snapshot" \
  > "$QDRANT_BACKUP_ROOT/restore-result.json"
curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION" \
  > "$QDRANT_BACKUP_ROOT/restored-collection-info.json"
curl --fail --silent --show-error \
  -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"filter":{"must":[{"key":"_ananta_record_type","match":{"value":"manifest"}}]},"limit":1,"with_payload":true,"with_vector":false}' \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/points/scroll" \
  > "$QDRANT_BACKUP_ROOT/restored-manifest.json"
python3 - "$QDRANT_BACKUP_ROOT" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
before = json.loads((root / "collection-info.json").read_text())
after = json.loads((root / "restored-collection-info.json").read_text())
before_result = before["result"]
after_result = after["result"]
for field in ("points_count", "vectors_count"):
    if before_result.get(field) != after_result.get(field):
        raise SystemExit(f"restored {field} differs")
before_vectors = before_result["config"]["params"]["vectors"]
after_vectors = after_result["config"]["params"]["vectors"]
if before_vectors != after_vectors:
    raise SystemExit("restored vector dimensions/distance differ")

def manifest(name):
    payload = json.loads((root / name).read_text())
    points = payload["result"]["points"]
    if len(points) != 1:
        raise SystemExit(f"{name} does not contain exactly one manifest")
    return points[0]["payload"]

if manifest("manifest.json") != manifest("restored-manifest.json"):
    raise SystemExit("restored scope or compatibility manifest differs")
PY
ALIASES_NOW="$(curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" "$QDRANT_URL/aliases")"
ALIAS_UPDATE="$(python3 - "$ALIASES_NOW" <<'PY'
import json
import os
import sys

aliases = json.loads(sys.argv[1])["result"]["aliases"]
name = os.environ["QDRANT_ALIAS"]
target = os.environ["QDRANT_COLLECTION"]
actions = []
if any(item["alias_name"] == name for item in aliases):
    actions.append({"delete_alias": {"alias_name": name}})
actions.append({
    "create_alias": {
        "alias_name": name,
        "collection_name": target,
    }
})
print(json.dumps({"actions": actions}, separators=(",", ":")))
PY
)"
curl --fail --silent --show-error \
  -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data "$ALIAS_UPDATE" \
  "$QDRANT_URL/collections/aliases" \
  > "$QDRANT_BACKUP_ROOT/alias-restore-result.json"
curl --fail --silent --show-error \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/aliases" \
  > "$QDRANT_BACKUP_ROOT/restored-aliases.json"
python3 - "$QDRANT_BACKUP_ROOT/restored-aliases.json" <<'PY'
import json
import os
import sys

aliases = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["aliases"]
matches = [
    item for item in aliases
    if item["alias_name"] == os.environ["QDRANT_ALIAS"]
]
if len(matches) != 1 or matches[0]["collection_name"] != os.environ["QDRANT_COLLECTION"]:
    raise SystemExit("restored alias verification failed")
PY
unset QDRANT_API_KEY
```

Only after an application-level read-only search verifies scope, compatibility
and expected results may the recovery environment be promoted.

## Wiki is a separate rollout domain

Wiki uses its own collection prefix, payload schema, configuration and override
history. Enabling CodeCompass does not enable Wiki. Activate Wiki explicitly:

```bash
set -euo pipefail
export WORKSPACE_ID=workspace-a
curl --fail --silent --show-error \
  -X PUT "$HUB_URL/api/vector-store/workspaces/$WORKSPACE_ID/override" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"domain":"wiki","override":{"provider":"qdrant"},"expected_revision":0}'
```

Rollback Wiki without changing CodeCompass:

```bash
set -euo pipefail
: "${WIKI_OVERRIDE_REVISION:?set the current Wiki override revision}"
curl --fail --silent --show-error \
  -X DELETE "$HUB_URL/api/vector-store/workspaces/$WORKSPACE_ID/override" \
  -H "Authorization: Bearer $HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "{\"domain\":\"wiki\",\"expected_revision\":$WIKI_OVERRIDE_REVISION}"
```

After either change, resolve both domains independently. Productive Wiki
composition is detailed in the [vector-index taskflow](qdrant-vector-index-taskflow.md).

## Stable reason and error codes

The complete bounded catalog and operator actions are maintained in
[Qdrant vector-store error codes](qdrant-vector-store-error-codes.md). Keep that
catalog aligned with the public config, endpoint, scope, compatibility,
fallback and Hub taskflow contracts.

## Integration and benchmark gates

Start the pinned service, install the optional client and run the real marker:

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export ANANTA_QDRANT_API_KEY="$(tr -d '\r\n' < "$ANANTA_QDRANT_API_KEY_FILE")"
export ANANTA_QDRANT_URL=https://localhost:6333
RUN_INTEGRATION_TESTS=1 \
  python -m pytest -q -m qdrant_integration \
  tests/test_qdrant_vector_store_integration.py
```

Each test uses a run-isolated prefix and `finally` cleanup. The real suite
covers create/rebuild, compatibility, alias swap, filtered search, batch
upsert, source-hash skip, delete, migration pause/resume, compatible JSON
fallback, Wiki scope isolation and a failed-rebuild injection that preserves
the former active alias. The CI job fails if any selected test is skipped.

Run the fixed-seed comparison only on an approved reference host:

```bash
set -euo pipefail
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export ANANTA_QDRANT_API_KEY="$(tr -d '\r\n' < "$ANANTA_QDRANT_API_KEY_FILE")"
QDRANT_CONTAINER_ID="$(docker compose \
  -f docker/compose-next/compose.qdrant.yml \
  --profile qdrant ps -q qdrant)"
test -n "$QDRANT_CONTAINER_ID"
python scripts/benchmark/qdrant_vector_store.py \
  --profile small \
  --reference-host-approved \
  --tls-ca-cert-file "$ANANTA_QDRANT_TLS_CA_FILE" \
  --container "$QDRANT_CONTAINER_ID" \
  --output artifacts/qdrant-vector-store-small.json
```

Profile v1 uses seed `424242` and exactly:

| Profile | Records | Queries | Dimensions | Payload bytes |
| --- | ---: | ---: | ---: | ---: |
| `small` | 10,000 | 100 | 384 | 512 |
| `medium` | 100,000 | 500 | 768 | 1,024 |
| `large` | 1,000,000 | 1,000 | 1,536 | 2,048 |

Each profile has two warmups and five measurements for build, refresh and
search. It records filtered/unfiltered exact-cosine Recall@10/50, p50/p95,
client RSS and Qdrant-container RSS. Memory is sampled at each phase start,
every second during the phase and again at phase end; an incomplete sampler
makes the artifact inconclusive. Recall gates use the worst measured query
while the mean remains reported. The artifact includes the observed server
version, exact observed image digest, client/Python versions, commit, profile
hash, CPU, RAM and OS.

`failed` exits `1`; `inconclusive` exits `2`; only `completed` exits `0`.
Missing reference-host approval, insufficient resources, missing container
memory, an unverified source commit, an unverified/mismatched server, an
unverified exact image reference or an aborted profile can never produce a
backend recommendation.

## Release evidence

The `qdrant-integration` job in `quality-and-docs.yml` records the observed
container image reference/ID, observed server version, client version, marker,
JUnit executed/skipped counts and cleanup state in the
`qdrant-integration-evidence` artifact. A job is releasable only when the real
marker executed with zero skipped tests and cleanup completed.

The CI artifact also proves that an intentionally inconclusive benchmark exits
non-zero and writes a structured artifact. That negative gate is not
performance evidence. A Qdrant recommendation requires a separately retained
`BenchmarkRunArtifact v1` whose status is `completed`; skipped, failed or
inconclusive runs must never be reported as passed. No completed reference-host
benchmark artifact is asserted by this document.
