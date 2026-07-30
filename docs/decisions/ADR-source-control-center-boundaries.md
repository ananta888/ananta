# ADR: Source Control Center Domain and Governance Boundaries

- Status: Accepted
- Date: 2026-07-30
- Scope: Source connections, immutable revisions, indexing and access grants

## Context

Ananta already has legacy source descriptors, a file-backed source registry,
knowledge-index jobs, context policies and version-qualified `SourceRef v2`
records. Their identities and authority boundaries were created for different
flows. Treating a mutable connection, an immutable revision, a citation
reference, an index and a policy grant as one source ID would make provenance,
revocation, destination changes and tenant isolation ambiguous.

The Source Control Center needs one additive domain model without replacing
the Hub task system or exposing secrets and authoritative policy facts to
Angular. The contracts must remain usable across isolated containers without a
shared mutable object graph.

## Decision

### 1. Canonical domain identities

The following concepts have separate IDs and lifecycles:

- `SourceConnection` is a mutable, tenant-, project- and owner-bound connector
  registration. Its canonical response contains a digest of server-held
  identity configuration, never a credential or raw secret.
- `SourceRevision` is an append-only snapshot resolved from one connection.
  Its revision digest, content-manifest binding, sensitivity and admission
  result never change. Refresh creates a new revision.
- `SourceRef` is an explicit provenance mapping to exactly one
  `SourceRevision`. It is not a connection ID or revision ID. The canonical v1
  mapping uses a digest-derived `sref_*` identity.
- `KnowledgeIndex` is an immutable materialization of one admitted
  `SourceRevision`, one index contract version and one policy snapshot.
- `IndexRun` is one attempt to build or verify a `KnowledgeIndex`. Retry,
  lease, progress, logs and failure belong to the run, not the immutable index
  identity.
- `ActiveIndex` is a Hub-owned, optimistically locked pointer from tenant,
  project and source connection to one verified index. Promotion and rollback
  update this pointer; they do not mutate an index or revision.
- `DestinationDescriptor` is the complete server-resolved worker, runtime,
  provider, model, model-class, provider-location and data-residency identity.
  Every coordinate participates in its digest-derived ID.
- `SourceAccessGrant` is a versioned binding of one source revision to one
  destination, operation, transformation, purpose, policy version and expiry.
  Provider or model changes produce a new destination ID, so an old grant
  cannot authorize the changed destination.

The neutral Python facade is `ananta_contracts.source_control`; equivalent
Draft-2020-12 wire schemas live in `schemas/source-control/`. Security-critical
objects are closed with `additionalProperties: false` and immutable Pydantic
models. Required `authority: hub` markers make canonical records distinguishable
from user commands. A command DTO is never accepted as a canonical record.

Existing `SRC_*` and `RUN_*` values remain evidence identifiers governed by the
source-grounding rules. The Hub may retain a supplied, verified legacy
`ananta.source_ref.v2` as an alias. It must never synthesize such an identifier
or infer it from a new `sref_*` ID.

### 2. Responsibility and container boundaries

The Hub is the sole control plane. It authenticates the caller, resolves tenant,
project and owner, validates a connection, resolves and admits a revision,
selects a destination, evaluates policy, issues or revokes grants, creates
index tasks, owns leases and promotes the active index.

A worker executes exactly one Hub-delegated task. It receives a closed
`DelegatedSourceManifestRef` containing only Hub-issued manifest, revision,
destination, grant and policy-version IDs plus the manifest digest. It neither
receives connector credentials nor resolves policy or another worker. It emits
evidence and artifacts for Hub verification and never creates tasks.

Angular is an untrusted control surface. It submits intent and renders
Hub-projected read models. Fields such as tenant, owner, revision admission,
provider, model, `cloud_effective`, `external_effective`, destination identity
and grant state are never accepted as authoritative client claims. Unknown or
unavailable policy state is deny/unavailable, not allow.

Shared code is limited to pure, versioned contracts, enums, ID derivation and
validation. Hub routes depend on Hub services and ports, never concrete worker
infrastructure. Workers depend on the neutral manifest contract, never Hub
routes, repositories or policy services. These boundaries protect SRP, ISP and
DIP while keeping connector, persistence and UI concerns independently
testable.

### 3. Admission, indexing and grant invariants

- A revision is indexable only after Hub admission binds the exact revision and
  content-manifest digests.
- A completed run is not active until the Hub verifies artifacts, revision,
  policy snapshot and destination, then atomically promotes `ActiveIndex` or
  records a reconciliable promotion event.
- Retrieval and context release require an active, unexpired, non-revoked grant
  matching the exact revision, destination, operation and transformation.
- Preview and dispatch resolve the same destination digest. Any change between
  them blocks dispatch and requires a new decision and grant.
- Disable and tombstone stop new use but preserve immutable evidence. Physical
  purge is a separate approved operation with reference checks.

## Additive migration and compatibility

Migration is staged and reversible:

1. Introduce the neutral v1 contracts and schemas without changing existing
   routes, source descriptors, registries, index jobs or context policies.
2. Under `SOURCE_CONTROL_CONTRACTS_V1`, project legacy records into canonical
   read models. Projection is read-only and records unmappable or unverifiable
   fields as unavailable; it does not invent identity or provenance.
3. Under `SOURCE_CONTROL_PERSISTENCE_V1`, dual-write canonical connection,
   revision, index and grant records behind repository ports. Compare counts,
   digests, tenant scope and lifecycle before enabling canonical reads.
4. Under `SOURCE_CONTROL_API_V1`, serve the new endpoints and Angular model
   while legacy routes remain aliases backed by compatibility adapters.
5. Enable canonical dispatch only after shadow policy decisions, index
   promotion reconciliation, security tests and audit comparisons pass.
6. Drain legacy writers, retain legacy readers for the rollback window, then
   remove an alias only after no supported client or stored record requires it.

The flag names define ownership and rollout intent; configuration tasks add
them only when their corresponding implementation exists. They default off.
Existing `source_descriptor.v1`, `source_catalog.v2`, `source_ref.v2`,
knowledge-index and context-policy payloads remain readable. No existing field
or endpoint is renamed or removed during adoption.

Rollback disables canonical reads and dispatch, stops new dual-writes, drains
in-flight canonical jobs and returns reads to the legacy adapter. Immutable
canonical records and audit evidence are retained for reconciliation. Rollback
must not copy secrets into legacy payloads, weaken tenant checks, reinterpret
unknown grants as allow or translate unverified IDs.

Legacy shutdown is allowed only when migration is idempotent, every active
record has a digest-matched canonical projection, dual-read results agree,
active-index pointers reconcile, no supported client uses the alias, rollback
has been exercised and grounded release evidence has been approved.

## Rejected alternatives

### One source ID for connection, revision and evidence

Rejected because mutable configuration would silently alter historical
provenance and revocation could not target one revision.

### Browser-computed destination or effective policy

Rejected because the client cannot authoritatively know runtime selection,
provider routing, data residency or current grants and could forge an allow.

### Worker-side connector resolution, policy or index orchestration

Rejected because it creates a second control plane and bypasses Hub queue,
tenant, budget, approval and audit ownership.

### Shared mutable source state between Hub and workers

Rejected because containers require explicit, reproducible boundaries and a
compromised worker must not gain connector credentials or write authority.

### Big-bang replacement of legacy source APIs

Rejected because it would force simultaneous client and data migration and
remove the safe comparison and rollback window.

## Consequences

The model adds explicit records and mapping work, but provenance, revocation,
destination changes, rollback and tenant scoping become deterministic. Closed
contracts intentionally require new schema versions for new authoritative
fields. Connector-specific optional data belongs in separately versioned
connector contracts, not unchecked extension maps.

No existing SOLID violation is moved into the shared facade. Compatibility
adapters temporarily carry translation complexity; each adapter has one legacy
format and depends on canonical ports rather than persistence or worker
implementations.

## Enforcement

- Golden and negative tests validate every v1 payload against JSON Schema and
  Python and prove that security-critical extra properties fail closed.
- Semantic tests prove provider/model changes alter destination identity and
  that revisions are immutable.
- Boundary tests reject Hub route, persistence and worker imports from the
  neutral facade.
- Worker payload tests permit only manifest-bound IDs and digests.
- Fixtures are deterministic and contain no invented `SRC_*` or `RUN_*`
  evidence identifiers.
- Later persistence, API, Angular and E2E tasks must preserve these invariants
  and provide real grounded evidence before release verification can pass.
