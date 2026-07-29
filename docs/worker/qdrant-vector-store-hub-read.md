# Qdrant Hub read-capability overlays

The Hub owns rollout, policy and the task queue. Workers execute delegated
index mutations. Direct Qdrant access from the Hub is limited to synchronous,
read-only retrieval and is disabled by default.

A Qdrant workspace override alone grants no network or secret access. Use a
domain-specific overlay only after authorizing the exact trusted scope. Both
overlays install the optional Qdrant client in the Hub image and mount the
existing API-key and private-CA secrets. Neither changes the mutation path.

Provision the [API/TLS files](qdrant-vector-store-tls.md) and
[Worker identity](qdrant-vector-worker-identity.md) before either stack.
Run from the repository root with a `.env` containing a non-empty
`INITIAL_ADMIN_PASSWORD`; the mandatory render fails before startup otherwise.

## CodeCompass reads

```bash
set -euo pipefail
test -s .env
: "${ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID:?set the authorized workspace ID}"
: "${ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID:?set the authorized repository ID}"
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
qdrant_stack=(docker compose --env-file .env \
  -f docker/compose-next/compose.stack.quickstart.yml \
  -f docker/compose-next/compose.workflow-runtime.dev-auth.yml \
  -f docker/compose-next/compose.qdrant.yml \
  -f docker/compose-next/compose.qdrant-workers.yml \
  -f docker/compose-next/compose.qdrant-hub-read.yml)
"${qdrant_stack[@]}" --profile qdrant config --quiet
"${qdrant_stack[@]}" --profile qdrant \
  up -d --build --wait --wait-timeout 180
```

The CodeCompass overlay sets `HUB_CAN_BE_WORKER=1` and
`ANANTA_CODECOMPASS_VECTOR_HUB_QDRANT_READ_ENABLED=true`. Both scope IDs are
required and must match the authorized workspace override.

## Wiki reads

```bash
set -euo pipefail
test -s .env
: "${ANANTA_WIKI_VECTOR_WORKSPACE_ID:?set the authorized Wiki workspace ID}"
: "${ANANTA_WIKI_VECTOR_SOURCE_ID:?set the authorized Wiki source ID}"
export ANANTA_QDRANT_API_KEY_FILE="$PWD/config/secrets/qdrant-api-key"
qdrant_stack=(docker compose --env-file .env \
  -f docker/compose-next/compose.stack.quickstart.yml \
  -f docker/compose-next/compose.workflow-runtime.dev-auth.yml \
  -f docker/compose-next/compose.qdrant.yml \
  -f docker/compose-next/compose.qdrant-workers.yml \
  -f docker/compose-next/compose.qdrant-wiki-hub-read.yml)
"${qdrant_stack[@]}" --profile qdrant config --quiet
"${qdrant_stack[@]}" --profile qdrant \
  up -d --build --wait --wait-timeout 180
```

The Wiki overlay passes the trusted Wiki scope, optional profile,
cache-state and manifest hash to the quickstart Hub. It sets
`ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED=true` and contains no CodeCompass
enablement or scope. Wiki refresh, rebuild, delete and migration still enter
the Hub-owned task queue and execute on a delegated worker.

Include both domain overlays only when both read capabilities are explicitly
authorized. Never copy either read-capability flag, the internal
`qdrant-worker` network or the secret mounts into the base quickstart stack.

## Task-bound retrieval scope

The overlay values are Hub deployment policy, never fields in a retrieval
request. Before a task-bound context bundle is built,
`WorkerJobService`/`ContextManagerService` asks the internal binding port to
materialize the authorized values as
`worker_execution_context.retrieval_vector_scope` on the authoritative Hub
task. The block is versioned, bound to the exact task ID and checked against an
authoritative Hub task. The logical VectorStore workspace is deliberately
separate from the task execution sandbox (`ws-<task>`). `RagService` reloads
the task and passes only the resulting typed `RetrievalVectorRuntimeScope` to
CodeCompass and Wiki retrieval.

Generic task create, orchestration-ingest and patch APIs reject callers that
try to set the reserved block. A patch of other worker-context fields
preserves an existing binding.
An explicitly supplied in-process scope is accepted for a task only when it is
identical to the authoritative binding. Missing bindings, task-ID mismatches
and execution-workspace mismatches fail closed; task-free chat, SGPT context
and CLI helper calls do not acquire Qdrant scope merely because an overlay is
installed.

The default binding provider supports the single deployment scope declared by
the environment variables above. When both CodeCompass and Wiki overlays are
installed, their workspace and profile must be identical. Multi-workspace
deployments must inject a Hub-owned
`RetrievalVectorScopeBindingProviderPort` backed by their authorized
task/workspace registry; no client payload may act as that provider.

Runtime cache keys include domain plus the full typed scope. Each read holds a
lease on the selected runtime; a rollout swap retires the old runtime but
defers `close()` until its in-flight reads release their leases.
