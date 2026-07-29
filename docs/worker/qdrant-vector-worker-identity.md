# Qdrant Vector worker identity

The Qdrant quickstart keeps Hub signing authority, Worker registration
credentials and Worker service credentials disjoint. The development identity
overlay is mandatory whenever `compose.qdrant-workers.yml` is used with the
quickstart stack. Production deployments use
`compose.workflow-runtime.production.yml` and operator-supplied secret files
instead.

## Provision the development identity

Create one private host root owned by the numeric user that runs the
containers. `create_host_path: false` makes a missing root fail before any
runtime service starts:

```bash
set -euo pipefail
export ANANTA_HOST_UID="$(id -u)"
export ANANTA_HOST_GID="$(id -g)"
export ANANTA_DEV_WORKFLOW_SECRET_DIR="$PWD/../ananta-data/workflow-runtime-dev"
install -d -m 0700 "$ANANTA_DEV_WORKFLOW_SECRET_DIR"
for identity_dir in hub worker alpha beta; do
  install -d -m 0700 "$ANANTA_DEV_WORKFLOW_SECRET_DIR/$identity_dir"
done
test "$(stat -c %u "$ANANTA_DEV_WORKFLOW_SECRET_DIR")" = "$ANANTA_HOST_UID"
test "$(stat -c %g "$ANANTA_DEV_WORKFLOW_SECRET_DIR")" = "$ANANTA_HOST_GID"
```

The `workflow-keyring-bootstrap` service runs without network access, creates
separate Hub, public-verification, Alpha-private and Beta-private subtrees, and
then exits. Existing complete credentials are reused. A keyring from before
Vector indexing is upgraded in place by adding only the known
`index_write`/`vector_index_operation` capability grants; tokens and keys are
not rotated. Incomplete or modified credential sets fail closed.

## Provision Vector dispatch attestation

The Hub receives the private Vector task signer. Workers receive only the
public verification keyring. Create the pair once; the generator refuses to
overwrite either file:

```bash
set -euo pipefail
export ANANTA_VECTOR_INDEX_TASK_KEYRING_DIR="$PWD/../ananta-secrets/vector-index"
export ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE="$ANANTA_VECTOR_INDEX_TASK_KEYRING_DIR/vector-index-task-signing-keyring.json"
export ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE="$ANANTA_VECTOR_INDEX_TASK_KEYRING_DIR/vector-index-task-verification-keyring.json"
if ! test -e "$ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE" \
  && ! test -e "$ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE"; then
  python3 scripts/generate_vector_index_task_keyrings.py \
    --output-dir "$ANANTA_VECTOR_INDEX_TASK_KEYRING_DIR"
fi
test -s "$ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE"
test -s "$ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE"
chmod 0600 "$ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE"
chmod 0644 "$ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE"
```

If exactly one keyring exists, stop and investigate; do not generate a new
pair over an active deployment. Rotate by provisioning a separately named
key ID and following a staged public-key overlap/revocation procedure.

## Effective boundary

The Hub starts with strict registered-Worker authentication and mounts only
the Hub subtree. Each Worker mounts the shared public verification subtree and
its own private subtree read-only. Worker service, registration and session
credentials are distinct, are never placed in Compose environment values, and
are never mounted into the other Worker. A Worker missing any credential or
the three `retrieval`, `index_write` and `vector_index_operation`
capabilities is not eligible for Vector dispatch.

The Qdrant Worker overlay probes
`/internal/worker/vector-index-readiness`. The endpoint exists only on a
Worker and returns HTTP 503 until local Vector handler composition succeeds,
all three capabilities have been advertised and a fresh successful Hub
registration confirms those same capabilities. The normal Hub health endpoint
remains independent and the Hub never exposes this Worker-only route.
