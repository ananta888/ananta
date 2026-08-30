# ADR: Native collaboration core before external bridges

Status: accepted for a default-off initial slice; release lanes remain blocked.

## Decision

Ananta implements durable collaboration as a Hub-owned bounded context. `CollaborationWorkspace`, Room,
ActorBinding, Membership and `WorkspaceEventV1` are transport-neutral. The Hub validates identity, policy,
membership, event admission and ordering. WebRTC/LiveKit remain live adapters. Buzz/Nostr remains an optional
bridge behind `CollaborationBridgePort` and cannot write task state or grant authority.

The initial persistence adapter is tenant-scoped SQLite with an append-only event sequence, idempotency,
transactional outbox row, membership revision, read cursor and presence lease. The application service depends
on a focused policy and store seam (SRP/DIP). Existing ShareSession APIs remain unchanged; a dry-run migration
planner provides the first additive compatibility seam.

## Automation

Native event admission, denial, revocation, cursor fencing, presence renewal, migration planning and bridge
disablement are deterministic Hub decisions. Tests and headless operation never wait for human approval. A
UI may display optional takeover/approval intents, but it is not required for correctness or completion.

## Limitations

This slice does not claim multi-participant live readiness, a production search engine, inbox consumers,
backup/restore readiness, Buzz compatibility or external conformance. Those release lanes remain fail-closed
until assignment-bound `SRC_*` and `RUN_*` evidence exists.
