# Qdrant vector-index task and artifact boundary

This document describes the mutation path shared by CodeCompass and Wiki.
The Hub remains the control plane. A Worker performs exactly one delegated
operation and never creates tasks or addresses another Worker.

## Execution flow

```text
authoritative records
        |
        v
Hub validates trusted scope and rollout
        |
        v
Hub atomically publishes a content-addressed document artifact
        |
        v
Hub queues one immutable task with path + SHA-256 + scope fingerprint
        |
        v
Hub issues one short-lived, selected-Worker dispatch attempt
        |
        v
selected Worker verifies signature, audience, expiry and one-time use
        |
        v
selected Worker redeems the current execute grant online at the Hub
        |
        v
selected Worker verifies root, path, type, size and SHA-256
        |
        v
Worker builds embeddings and prepared points
        |
        v
Worker writes JSON or Qdrant in bounded batches
        |
        v
Hub validates and persists the bounded terminal result
```

Search is not part of this path. It is read-only and cannot publish an input,
create a task or mutate a collection.

## Hub-origin task attestation

Vector-index mutations use a closed, versioned task envelope. Generic task
create, patch and orchestration-ingest boundaries reserve and reject:

- `source=vector_index`;
- `task_kind=vector_index_operation`;
- `worker_execution_context.vector_index_task`.

Only `VectorIndexTaskService` may compose the trusted scope, rollout policy,
artifact reference and immutable intent. It signs the complete envelope with
Ed25519. The Hub receives the private signing keyring; Workers receive only a
public verification keyring whose schema rejects private or symmetric key
material. The shared JWT `SECRET_KEY` is not used for this boundary.

Generate the initial pair before starting the Qdrant worker overlay:

```bash
umask 077
VECTOR_INDEX_KEY_DIR="${ANANTA_VECTOR_INDEX_TASK_KEY_DIR:-$PWD/../ananta-secrets/vector-index}"
python3 scripts/generate_vector_index_task_keyrings.py \
  --output-dir "$VECTOR_INDEX_KEY_DIR" \
  --key-id vector-index-task-key-v1
export ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE="$VECTOR_INDEX_KEY_DIR/vector-index-task-signing-keyring.json"
export ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE="$VECTOR_INDEX_KEY_DIR/vector-index-task-verification-keyring.json"
```

The generator refuses to overwrite existing files. The Compose overlay mounts
the private file only into `ai-agent-hub` and the public file only into
`ai-agent-alpha`/`ai-agent-beta`. Non-Compose processes use
`ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE` on the Hub and
`ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE` on a Worker.
The default directory is outside the repository and Docker build context.
`config/secrets/` is also denied by both `.gitignore` and `.dockerignore`;
never override these boundaries or bake keyrings into an image.
The Hub accepts its private keyring only with owner-only `0400` or `0600`
permissions. Public Worker keyrings may remain `0444` or `0644`.

The Worker verifies the signature and the closed envelope fields before store
construction, artifact access, secret resolution or any outbound call. It
also binds the signed `job_id` to the outer queued task ID. Missing, malformed,
revoked or modified attestations fail closed. Retry revalidates current Hub
preparation policy and signs the updated checkpoint/preparation envelope
again; it never reuses an invalid signature.
Attestation v2 signs `schema`, `algorithm` and `key_id` as a protected Ed25519
header. Relabeling one signature to a different key ID therefore fails even
when both IDs refer to the same public key material; duplicate key material in
one keyring is rejected as invalid configuration.

An eligible Worker advertises the dedicated `vector_index_operation`
capability only after the public keyring, durable replay ledger, Hub admission
client and execution adapter have all been composed successfully. The Hub
requires `retrieval`, `index_write` and `vector_index_operation` in both the
Worker's advertised capabilities and its strict registration-keyring
allowlist. Without that proof, the handler is not registered and no execute
grant can be redeemed.

The queued envelope is not itself executable. Immediately before forwarding a
proposal or execution, the Hub signs a nested
`ananta.vector_index_task_dispatch.v1` grant containing a random `attempt_id`,
monotonic sequence, exact Worker URL audience, phase, issue time and expiry.
The default lifetime is 300 seconds and may be bounded from 30 through 3,600
seconds with `ANANTA_VECTOR_INDEX_TASK_DISPATCH_TTL_SECONDS`. Workers compare
the exact audience with `ANANTA_VECTOR_INDEX_TASK_AUDIENCE` (or `AGENT_URL`)
and reject a proposal grant for execution.

Before calling the execution port, each Worker first atomically consumes the
signed `job_id + attempt_id + sequence + phase + audience` receipt in its own
SQLite replay ledger. It then authenticates to the Hub with its strict,
file-managed Worker service token and redeems that exact execute grant through
the internal admission endpoint. The Hub atomically rechecks the current task,
attempt, sequence, phase, assignment, audience, expiry and terminal state. A
Hub cancellation that wins before admission therefore revokes the grant, and
the Worker performs no mutation. Admission is single-use; there is no second
redemption request during result delivery. Instead, the persisted admission
receipt must still match the exact attempt, sequence, phase and audience when
the Hub performs the terminal result compare-and-set. Proposal grants are
consumed locally but never receive mutation admission. Because local replay
consumption happens before the online admission request, any denial, timeout or
invalid response burns that local grant; retry through the Hub with a fresh,
higher dispatch sequence rather than resending the same envelope.

Dispatch issuance and admission are one mutually exclusive Hub transition.
The issuance compare-and-set binds the complete previous dispatch and the
absence of an admission receipt. Admission binds that same current dispatch
and also requires the receipt to be absent. If admission wins, a concurrent
reissue fails without replacing the admitted attempt; if reissue wins, the
stale admission fails without creating a receipt for the new attempt.

A durable per-job high-watermark rejects dispatch sequences that are equal to
or lower than an already consumed grant, including out-of-order delivery after
a restart. The default path is
`/app/data/vector-index-replay/vector-index-task-replay.sqlite3`; its private
parent directory is created with owner-only permissions even when `/app/data`
itself is a Docker volume. Every existing path component is opened without
following symlinks. A group- or world-writable ancestor is accepted only when
it is a sticky directory owned by root or the Worker UID; the direct ledger
parent must always be owned by root or the Worker UID and have mode `0700`.
Configure `ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE` only to another durable,
Worker-private path.

Detailed expired attempt receipts are retained for 86,400 seconds by default.
`ANANTA_VECTOR_INDEX_TASK_REPLAY_RECEIPT_RETENTION_SECONDS` may set an explicit
retention from 3,600 through 31,536,000 seconds. Cleanup uses the Worker clock,
not an incoming dispatch timestamp. The per-job sequence high-watermark is a
permanent compact tombstone and is never pruned: deleting it would allow an old
signed sequence after a restart or clock rollback. To retire a ledger, first
remove that Worker identity from service, revoke every signing key that could
have issued one of its grants, wait through the maximum dispatch and retention
windows, then archive the complete owner-only ledger offline. Never truncate or
replace a live ledger. The ledger survives process restarts. A signed exact
HTTP(S) origin audience prevents the same attempt from being moved to a
different Worker, so no Worker-to-Worker coordination or shared control-plane
database is introduced.

Every terminal result carries the consumed `attempt_id`. The Hub accepts it
with a compare-and-set only when it equals the currently persisted admitted
dispatch. Byte-equivalent duplicate delivery is an idempotent no-op; a
conflicting duplicate is rejected. A retry generates a new attempt and clears
the prior admission receipt; a delayed result from an older attempt cannot
complete the new run. An admitted task cannot receive another dispatch. If
transport fails after execution and the original bounded result cannot be
redelivered, cancel it through the Hub and use the Hub retry transition with
the same idempotency key instead of replaying captured dispatch bytes.

Submission and retry also hold a canonical scope-fingerprint mutation fence
from the authoritative re-read through the queue/CAS write. The shared fence
combines an in-process lock with a PostgreSQL advisory lock, so independent Hub
service instances cannot both activate tasks for one scope. A concurrent
byte-equivalent request remains idempotent and returns the first task; a
different idempotency key fails with `vector_index_task_conflict`. Failure to
acquire the database-backed fence fails closed instead of falling back to the
previous non-atomic repository scan.

Generic task execution/result paths cannot override
`task_kind=vector_index_operation`, project schema-free terminal output or
pause/resume this domain. Generic cancel/retry and cleanup operations delegate
to the same Hub lifecycle service, so they cannot bypass admission revocation
or result CAS.

For key rotation, first distribute a verification keyring containing both the
old and new public keys and rolling-restart the Workers, then activate the new
private key on the Hub and restart the Hub. Keep the old public key until every
old task and its retry window is terminal; only then publish its revocation
and rolling-restart all processes that cache the keyring. Never mount a
private keyring into a Worker.

## Why documents, not vectors, cross the boundary

Embedding generation is execution work. The Hub therefore does not call
`embed_texts()` when a delegated CodeCompass or Wiki mutation is configured.
It publishes normalized source documents. The Worker receives an explicit
preparation specification and creates vectors immediately before the store
operation.

The preparation contract is closed and versioned:

- schema: `ananta.vector_index_preparation.v1`;
- kinds: `codecompass_documents` or `wiki_documents`;
- provider identity, model version, dimensions and text profile must equal
  the task compatibility contract;
- the kind must match the trusted task domain;
- external embedding calls require HTTPS, an exact base-URL allowlist,
  explicit external-call approval and an `env://` or `secretfile://`
  reference; plaintext keys are rejected.

These fields do not grant network access. The Hub applies the separate,
immutable `VectorIndexPreparationPolicyPort` before queue persistence.
`local_hash` remains offline and needs no profile. An external preparation
must name `embedding.policy_profile`, and its complete normalized embedding
mapping must equal that deployment profile. In particular, caller values for
`external_calls_allowed`, `base_url`, `allowed_base_urls` and `api_key_ref`
can only match the profile; they can never widen it. Submission and retry
fail with `vector_index_embedding_policy_forbidden` after a mismatch.

The Hub profiles and Worker egress policy are separate deployment inputs:

```bash
export ANANTA_VECTOR_INDEX_EMBEDDING_PROFILES_JSON='{
  "approved-external": {
    "domains": ["codecompass", "wiki"],
    "embedding": {
      "provider": "openai_compatible",
      "provider_id": "openai_compatible",
      "model": "text-embedding-3-small",
      "model_version": "text-embedding-3-small",
      "dimensions": 1536,
      "base_url": "https://embeddings.example.test/v1",
      "api_key_ref": "secretfile:///run/secrets/embedding-api-key",
      "timeout_seconds": 20,
      "external_calls_allowed": true,
      "allowed_base_urls": ["https://embeddings.example.test/v1"]
    }
  }
}'
export ANANTA_VECTOR_INDEX_EMBEDDING_EGRESS_ALLOWLIST_JSON='[
  "https://embeddings.example.test/v1"
]'
```

JSON keys and lists must already be canonical: provider aliases, duplicate or
unsorted lists, URL credentials, percent-encoded/path-traversal variants,
non-HTTPS URLs and implicit URL rewrites are rejected. The profile key is the
task's `policy_profile`; it is deliberately absent from the profile's
`embedding` object because the Hub binds it from the key. Mount the referenced
secret only on eligible Workers. The only supported environment reference in
the default Worker resolver is `env://ANANTA_EMBEDDING_API_KEY`; prefer a
dedicated `secretfile://` mount for container deployments.

Workers do not trust the Hub task flag as their egress policy. Each Worker
captures `ANANTA_VECTOR_INDEX_EMBEDDING_EGRESS_ALLOWLIST_JSON` once at
composition time and checks it before store construction, artifact loading,
secret resolution or provider construction. A missing/invalid/mismatching
allowlist fails with the same stable denial code, so no embedding request is
sent and no secret is resolved. Delegated embedding providers also disable
HTTP redirects so an approved origin cannot redirect the authorization header
to an origin outside that allowlist.

CodeCompass and Wiki use separate typed document preparers. They do not share
an untyped payload mapper. Store adapters still receive only prepared points
and remain independent of embedding and task orchestration.

## Artifact transport

The Qdrant worker overlay declares the named
`qdrant-vector-index-inputs` volume:

- the Hub mounts `/var/lib/ananta/vector-index-inputs` read/write and receives
  `ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT`;
- Workers mount the same path read-only and receive
  `ANANTA_VECTOR_INDEX_INPUT_ROOTS`;
- one pure locator derives the canonical full scope fingerprint and the only
  accepted path, `<domain>/<scope-fingerprint>/<sha256>.json`;
- filenames are the SHA-256 of the immutable JSON bytes;
- publication uses a same-directory temporary file, `fsync` and atomic
  replacement;
- an existing artifact is reused only after a bounded digest verification;
- the publisher returns `path`, `sha256` and `scope_fingerprint`;
- the Hub rejects a reference unless all three values exactly match the
  trusted task scope and canonical locator result;
- Workers repeat that binding check before store construction and before
  every file open, then reject absolute paths, traversal, symlinks,
  non-regular files, digest mismatches, malformed JSON, more than 100,000
  documents or more than 64 MiB.

The queue contains only the canonical relative path, digest and full scope
fingerprint. It does not contain the document set or generated vectors. The
volume is an explicit container boundary, not an assumption that Hub and
Worker data directories are shared. Do not replace it with a path inside
`hub-data`, `alpha-data` or `beta-data`.

Migration uses the same reference contract. The legacy
`migration.source_path` field is forbidden: it cannot carry a digest or prove
that a source belongs to the Hub-trusted scope. A migration source must first
be published and then supplied as the resulting scope-bound `input_ref`.

Content-addressed files support retries with the same idempotency key. Remove
old artifacts only after all tasks referencing them are terminal and outside
the deployment's retry/retention window. Never delete the whole volume as part
of a scope reset.

## CodeCompass

`CodeCompassVectorRetrievalService.refresh_index()` loads and normalizes the
authoritative embedding records in the Hub, then publishes the document
bundle. The Worker applies
`codecompass-symbol-path-summary-v1`, creates deterministic prepared points
under the trusted CodeCompass scope and executes refresh/rebuild.

For Qdrant, a productive runtime therefore requires both:

- the Hub-owned `VectorIndexTaskService`;
- the explicit input publisher configured by the worker overlay.

Local JSON remains the compatibility default. Its direct local refresh keeps
the existing synchronous behavior and needs neither Qdrant nor the optional
client.

## Wiki

Wiki uses domain `wiki`, collection prefix `ananta-wiki` and payload schema
`ananta.wiki_vector_payload.v1`. The rollout resolver derives the separate
prefix and rejects a CodeCompass prefix. A Wiki override never activates
CodeCompass, and vice versa.

The productive Wiki composition is
`build_wiki_retrieval_index_service()` with a
`HubWikiVectorRuntimeResolver`. Required trusted environment fields are:

- `ANANTA_WIKI_VECTOR_WORKSPACE_ID`;
- `ANANTA_WIKI_VECTOR_SOURCE_ID`;
- optional `ANANTA_WIKI_VECTOR_PROFILE_NAME`;
- optional cache-state and manifest hashes.

Qdrant reads by the Hub additionally require the explicit
`ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED=true` capability and the same
network/secret isolation used for other Hub-as-worker reads. Wiki refresh,
rebuild and delete remain Hub-owned tasks even when reads are enabled.

## Result boundary

Worker results are untrusted input. Before persistence the Hub enforces:

- the exact result schema, job, current dispatch attempt, operation and
  idempotency key;
- terminal status only;
- bounded depth, object/list entries, key and string bytes, and 64 KiB total;
- finite JSON scalar values only;
- rejection of secret patterns, authorization material, document text,
  prompts, outputs, embeddings and vectors;
- a generic error string rather than free Worker exception text;
- atomic exclusion between cancellation and result acceptance.

Failed validation leaves the task state and verification payload unchanged.
Retries reuse the original task intent, trusted scope and idempotency key.

## SOLID boundary

The design preserves:

- SRP: publisher, loader, preparation strategies, embedding policy, task
  lifecycle and store adapter each own one concern;
- OCP: new document types register a narrow preparation strategy;
- ISP: stores depend on capability-specific ports and optional planned writes;
- DIP: Hub and Worker services depend on publisher, task, preparation,
  preparation-policy and store ports rather than concrete Qdrant clients.

No Worker-to-Worker path or independent orchestration loop is introduced.
