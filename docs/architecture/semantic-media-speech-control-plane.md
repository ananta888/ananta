# Semantic media and speech control plane

Status: normative architecture contract. All capabilities remain disabled by default.

Ananta preserves the Hub–worker architecture. The Hub owns identities, permissions, consent snapshots, contracts, epochs, leases, the task queue, scheduling, admission, revocation and publication decisions. Browsers capture or render media and may execute explicitly granted peer-local transforms. Workers execute only bounded Hub tasks and return artifacts. A relay or SFU transports authorized ciphertext and never schedules work.

```text
Browser A -- encrypted media/data --> relay or SFU -- encrypted media/data --> Browser B
    |                                  (transport only)                         |
    +-- authenticated control ----------> Hub <--------- authenticated control--+
                                           |
                                      Hub task queue
                                           |
                     +---------------------+--------------------+
                     v                     v                    v
                voice worker       reconciliation worker   training worker
```

## Operating modes and fallback

| Mode | Media plaintext | Semantic plaintext | Control owner | Required fallback |
|---|---|---|---|---|
| Strict E2EE | originating and receiving browsers only | browsers only | Hub | encrypted ordinary peer media/data |
| Ordinary encrypted media | browsers; WebRTC endpoint semantics apply | none | Hub | Hub relay for bounded encrypted payloads |
| Consented server-worker | specifically admitted isolated worker | specifically admitted isolated worker | Hub | ordinary Voice/Pair path remains healthy |

Strict E2EE never silently falls back to a server-readable mode. A server worker is a separately consented purpose, scope and retention decision. Disabling background sync, reconciliation, training or adapter routing does not terminate an ordinary Voice or Pair session.

## Plaintext access matrix

| Data class | Browser origin | Browser receiver | Hub | Relay | SFU | Voice worker | Reconciliation worker | Training worker |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Audio/video | yes | yes | no | no | no | consent + task | consent + task | consent + task |
| Transcript | yes | yes | control metadata only | no | no | consent + task | consent + task | curated records only |
| Semantic features | yes | yes | digest/size only | no | no | task-specific | task-specific | curated records only |
| Evidence | local evidence | admitted evidence | lineage metadata | no | no | no | consent + task | admitted dataset only |
| Checkpoint | no | no | opaque artifact metadata | no | no | no | own task only | own task only |
| Dataset | no | no | manifest/lineage | no | no | no | admitted output only | task-scoped input only |
| Adapter | verified artifact only | verified artifact only | registry metadata | no | no | no | no | own unpublished output |
| Keys | local keys only | local keys only | envelope/control keys only | no | no | task envelope only | task envelope only | task envelope only |

Every worker plaintext read requires an active Hub task, a closed contract, a current consent version, a current key epoch and an unexpired attempt lease. Output publication repeats those checks. Container filesystems and temporary workspaces are not shared implicitly.

## Authoritative state and transactional audit

Every persistent semantic-media authority belongs to the Hub database: membership and contracts, leases, SFU admission and epochs, relay cursors, consent and evidence, peer transfers, reconciliation, adaptation, datasets, training attempts and adapter approval state. A domain service prepares a closed, content-free transition event before mutation. Its repository commits the state change and that event to the SQL audit outbox in the same transaction; a failed outbox append therefore rolls back the authority mutation.

The background audit reconciler only projects committed outbox rows to the append-oriented audit sink. Sink outages leave bounded retry state and cannot create an unaudited authority transition. Event IDs and domain idempotency keys make replay and multi-Hub recovery exactly-once at the public audit boundary. Raw media, transcripts, features, keys, local paths and partner identifiers are forbidden in both outbox and sink payloads.

This split protects SRP and DIP: domain services decide transitions, repositories own transaction boundaries, and the reconciler owns delivery. No service may compensate for a committed authority mutation by emitting a later best-effort `record_transition` call.

## Prohibited orchestration

- A peer cannot create, assign or complete a task; it can only make a bounded request to the Hub.
- A worker cannot instruct a peer, enqueue another worker or import Hub schedulers.
- Workers never exchange tasks or artifacts directly; the Hub validates and routes every handoff.
- An SFU cannot trigger training, reconciliation or compute. It has no task-queue credentials.
- Prompt or tool content cannot widen permissions, consent, contracts, capabilities or feature flags.

These boundaries protect SRP and DIP: domain policy remains behind Hub-owned ports, while browser, transport and worker adapters have small execution-focused interfaces.
