# ADR: Native collaboration core before external bridges

Status: accepted and implemented for the default-off local/single-Hub Native Core; optional release lanes remain independent.

## Decision

Ananta implements durable collaboration as a Hub-owned bounded context. `CollaborationWorkspace`, Room,
ActorBinding, Membership and `WorkspaceEventV1` are transport-neutral. The Hub validates identity, policy,
membership, event admission and ordering. WebRTC/LiveKit remain live adapters. Buzz/Nostr remains an optional
bridge behind `CollaborationBridgePort` and cannot write task state or grant authority.

The persistence adapter is tenant-scoped SQLite with append-only sequencing,
idempotency, transactional outbox/inbox, projection checkpoints, restricted
rooms, membership/history, read cursor and epoch-bound presence. Focused Hub
services own policy, evidence, delivery, search, resource/command control,
migration and recovery (SRP/DIP). Existing ShareSession APIs remain unchanged;
server-read migration is dry-run-first, revision-bound, idempotent and
observe-only.

## Automation

Native event admission, denial, revocation, cursor fencing, presence renewal, migration planning and bridge
disablement are deterministic Hub decisions. Tests and headless operation never wait for human approval. A
UI may display optional takeover/approval intents, but it is not required for correctness or completion.

## Release boundary

Local tests cover multi-receiver routing semantics, bounded backpressure,
search, delivery and backup/restore, but do not claim a real SFU/TURN,
multi-region or Buzz relay run. Multi-Hub, Live and Buzz production lanes remain
fail-closed until their exact environment produces Hub-registry-bound
`SRC_*`/`RUN_*` evidence. This limitation does not make a person a prerequisite
for tests or bounded operation.
