# ADR: Hub-governed peer overlay with media no-go

- Status: accepted
- Date: 2026-08-29
- Scope: decentralized WebRTC group topology

## Decision

Ananta keeps the Hub as the only authority for membership, epochs, topology,
route leases, one-use link tickets, feature flags and release decisions. Peers
execute signed edges; they do not create membership, extend leases, elect a
global topology or orchestrate workers.

The transport choices remain independent:

| Path | Decision | Safe fallback |
| --- | --- | --- |
| 1:1 media/data | existing direct path | TURN or control-only |
| Small-group media | resource-gated mesh, hard maximum four participants | LiveKit E2EE |
| Larger-group media | LiveKit E2EE | control-only |
| Opaque encrypted data | default-off, bounded Hub-issued peer DAG | Hub/control path |
| Cross-PeerConnection encoded media relay | no-go | LiveKit E2EE |

QoS does not weaken this split. Peer-DAG queues accept only control, rekey,
event, semantic and bulk ciphertext. Authenticated simulcast/SVC layer
narrowing is available solely to direct-mesh and LiveKit E2EE adapters; it is
not an authorization to forward encoded media across PeerConnections.

The media-relay no-go follows the current Encoded Transform ownership model:
an encoded frame belongs to its producing stream and cannot be moved to another
stream. Ananta therefore does not claim portable RTP ciphertext fanout without
decode/re-encode. The existing ANME framing is not represented as RFC 9605
SFrame compliance. A future standards-backed adapter requires a new ADR and
assignment-bound source and runtime evidence.

## Contracts

Membership, key, route and topology epochs are separate. Membership changes
also rotate the key epoch. A topology change advances route and topology epochs
without implicitly rotating content keys. Route leases bind tenant, room,
publication, child, primary and backup parent, capabilities, traffic classes,
expiry and epoch. Link tickets bind exactly one edge and are short-lived and
one-use at the Hub.

Opaque relay code has no decrypt or key-export port. It accepts only a
Hub-validated lease projection, enforces scope, route epoch, TTL, chunk bounds,
digest, path, hop limit, replay budget and per-child/per-class queues. Immutable
origin content is signature-verified at every hop; the mutable hop/path envelope
is constrained independently by every authenticated Hub-leased edge.

## Consequences

- Data overlay is disabled unless `ANANTA_PEER_OVERLAY_DATA_ENABLED=true`.
- Direct peer modes can expose network metadata to their immediate peers;
  E2EE and mDNS do not justify a contrary privacy claim.
- Missing NAT, churn or malicious-relay evidence stays fail-closed. Headless
  browser capacity tests use Hub-issued synthetic test `SRC_*`/`RUN_*`
  identities and can never satisfy a production release gate.
- LiveKit and TURN remain independent, supported fallbacks.

## SOLID check

Topology selection, durable state, signed contracts, relay-health policy,
browser connection ownership and opaque forwarding use separate components.
The Hub service composes these ports and remains the single application-level
authority. No worker-to-worker control path or shared-process assumption is
introduced.
