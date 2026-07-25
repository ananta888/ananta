# Qdrant vector store

Ananta supports Qdrant as an optional worker-side vector-store backend. The
default remains the local JSON backend. The hub owns rollout, policy, tasks and
queue state; a worker executes one delegated index operation and never
orchestrates another worker.

The supported pair is Qdrant Server `1.18.2` with `qdrant-client==1.18.0`.
Runtime code uses `query_points`, not the removed legacy search API. See the
[Qdrant documentation](https://qdrant.tech/documentation/) for server concepts.

## Install and start

Install the optional Python dependency:

```bash
pip install -e '.[qdrant]'
```

Create a secret file before starting the profile:

```bash
install -d -m 700 config/secrets
openssl rand -hex 32 > config/secrets/qdrant-api-key
chmod 600 config/secrets/qdrant-api-key
docker compose \
  -f docker/compose-next/compose.stack.quickstart.yml \
  -f docker/compose-next/compose.qdrant.yml \
  --profile qdrant up -d
```

The overlay pins the image by tag and digest, publishes REST and gRPC only on
the host loopback interface, mounts the API key as a Docker secret, uses a
private worker network and persists database and snapshot data in named
volumes. It also configures a health check, restart policy, PID limit and
resource limits. Stopping the stack does not remove the volumes.

Inside the Compose worker network, the REST origin is
`http://qdrant:6333`. A host process uses `http://127.0.0.1:6333`. Each exact
origin must be allowlisted. A non-local origin additionally requires
`external_calls_allowed=true`; TLS verification cannot be disabled. API keys
must use an `env://NAME` or `file:///absolute/path` reference and are never
accepted inline.

## Rollout and task ownership

Effective configuration has this precedence:

1. Global JSON default.
2. Explicit profile override.
3. Explicit workspace override.

Qdrant is therefore opt-in. The hub validates and stores each override, records
a secret-free audit entry and sends an immutable resolved configuration to the
selected worker. CodeCompass and Wiki use separate rollout domains, collection
prefixes and rollback histories. Enabling one does not enable the other.

The hub control API exposes resolved configuration and authenticated
profile/workspace override and rollback operations. It also accepts typed
`index`, `refresh`, `rebuild`, `delete` and `migrate` jobs. The hub owns
idempotency, per-scope serialization, prioritization, cancellation and retry.
Retry resumes under the same idempotency key. Search is read-only and never
creates a task, collection or migration.

Use a dry-run migration before a JSON-to-Qdrant migration. A migration validates
schema, dimensions, distance, provider and model compatibility, preserves the
source file, writes into a versioned staging collection and activates it only
through an atomic alias swap. Interrupted batches return a checkpoint. A
configuration rollback to JSON requires no code change.

Common stable reason codes:

| Reason | Meaning | Action |
| --- | --- | --- |
| `collection_missing` | No active collection exists for the scope | Submit a rebuild |
| `dimensions_mismatch` | Query/index dimensions differ | Rebuild with the active embedding profile |
| `migration_required` | Stored schema is not compatible | Run dry-run, then migrate or rebuild |
| `fallback_state_incompatible` | JSON fallback metadata differs | Rebuild JSON or disable fallback |
| `vector_store_endpoint_not_allowlisted` | Endpoint is outside the exact allowlist | Correct the allowlist |
| `vector_store_external_calls_not_allowed` | A remote endpoint lacks explicit opt-in | Enable only after review |
| `vector_store_secret_not_found` | Referenced secret is unavailable | Repair the mount or environment |

Availability policy is explicit: fail fast, return a degraded empty result, or
use the configured JSON fallback only when its compatibility state matches.
Fallback decisions expose bounded reason codes and backend labels, never scope
IDs, payload text, URLs or secrets.

## Backup, restore and scoped reset

Create Qdrant snapshots through the authenticated snapshot API and copy them
from the `qdrant-snapshots` volume to protected storage. Record the active alias
map separately: Qdrant collection snapshots do not include aliases.

For restore:

1. Restore into a new versioned collection.
2. Validate point count, dimensions, distance and compatibility metadata.
3. Reconstruct the recorded alias with one atomic alias update.
4. Keep the former collection until application checks pass.

Never delete collections by an unscoped prefix in production. Reset only the
workspace/repository/profile/domain collection selected by the hub. Removing
the whole `qdrant-data` volume is a destructive local-development reset and
must not be used for shared environments.

## Integration test and benchmark

The real integration test is disabled by default:

```bash
RUN_INTEGRATION_TESTS=1 \
ANANTA_QDRANT_API_KEY="$(cat config/secrets/qdrant-api-key)" \
pytest -m integration tests/test_qdrant_vector_store_integration.py
```

It uses a unique collection prefix and deletes aliases and collections in a
`finally` cleanup. It covers rebuild, unfiltered and filtered search, upsert,
delete and atomic alias replacement.

Run the fixed-seed comparison:

```bash
ANANTA_QDRANT_API_KEY="$(cat config/secrets/qdrant-api-key)" \
python scripts/benchmark/qdrant_vector_store.py \
  --profile small \
  --reference-host-approved \
  --container "$(docker compose \
    -f docker/compose-next/compose.stack.quickstart.yml \
    -f docker/compose-next/compose.qdrant.yml ps -q qdrant)" \
  --output artifacts/qdrant-vector-store-small.json
```

Versioned profiles use seed `424242`: `small` is 10,000 records, 100 queries,
384 dimensions and a 512-byte synthetic payload; `medium` is 100,000/500/768/
1,024; `large` is 1,000,000/1,000/1,536/2,048. Every profile performs two
warmup runs and five measured runs for build, refresh and search. Filtered and
unfiltered exact-cosine Recall@10 and Recall@50, p50/p95 latency, client RSS
and Qdrant container RSS are recorded with peak phase and timestamp.

Output conforms to `BenchmarkRunArtifact v1` and includes commit, profile hash,
Qdrant image digest, client/Python versions, CPU, RAM and OS. Missing reference
host approval, insufficient resources, an aborted profile or missing container
memory makes the affected run or metric `inconclusive`, never passed. Backend
recommendations are emitted only for a complete non-inconclusive artifact;
absolute latency, memory and recall budgets are part of the versioned profile.
