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

This closes `DG-01` and `DPM-POC-002` as a documented **no-go**, not as a
successful relay implementation. The normative `writeEncodedData` algorithm in
the W3C WebRTC Encoded Transform Working Draft checks the frame owner and says
that a processor cannot create frames or move frames between streams. Because
the only permitted standard path terminates at that boundary, Ananta does not
instantiate a relay with a content key, decoder or cleartext fallback merely to
manufacture the two-child success branch. RFC 9605 specifies SFrame protection;
it does not add the missing cross-`RTCPeerConnection` browser primitive.

## DG-03 publication decision

The Hub applies the following publication-specific decision. `canary_only`
means an explicitly allowlisted, default-off test or synthetic-evidence run; it
does not mean production release evidence. Ordinary small-group mesh is a
separate endpoint topology and is not counted as Peer-DAG media.

| Publication class | Server egress | Total traffic / hop latency | Client CPU / battery | NAT success | Operations | DG-03 decision |
| --- | --- | --- | --- | --- | --- | --- |
| Opaque events, artifact chunks and semantic data | potentially reduced, test-only | bounded two-hop queues exist; real WAN latency unverified | bounded queue/load tests; device cost unverified | unverified | new route/lease observability required | `canary_only` |
| Audio-only Peer-DAG media | no portable relay path | WebCodecs/DataChannel experiment is bounded, not RTP parity | measured only in synthetic browsers | unverified | custom jitter/render/congestion stack | `LiveKit_only` |
| Active-speaker video | no portable relay path | hop and switch recovery unverified | encoder and battery cost unverified | unverified | custom speaker and keyframe control | `LiveKit_only` |
| Screenshare | no portable relay path | lossless readability and recovery unverified | encoder and uplink cost unverified | unverified | custom capture/render control | `LiveKit_only` |
| Single-publisher video broadcast | no portable relay path | no production Peer-DAG sample | device fanout cost unverified | unverified | LiveKit path already operated | `LiveKit_only` |
| Many-to-many video | not inferred from broadcast | multiplicative traffic and hops unverified | multiplicative encoder/uplink cost unverified | unverified | highest topology complexity | `LiveKit_only` |

The Hub-reserved headless `DPM-POC-003` run evaluates Chromium and Firefox with
VP8 and Opus WebCodecs over `ordered=false`, `maxRetransmits=0` DataChannels. It
measures transport latency, loss, audio/video timestamp error, injected delta
loss and keyframe recovery. Both engines currently execute the bounded
experiment, but application-level media congestion control and renderer parity
remain unverified. The result therefore cannot promote any media class above
`LiveKit_only`. Its synthetic test-scoped `SRC_*` and `RUN_*` identities are
issued by the Hub registry before execution and are explicitly ineligible for
production release.

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

Primary standards reviewed for these decisions:

- <https://www.w3.org/TR/webrtc-encoded-transform/#abstract-opdef-writeencodeddata>
- <https://www.w3.org/TR/webcodecs/>
- <https://www.w3.org/TR/webrtc-stats/>
- <https://www.rfc-editor.org/rfc/rfc9605.html>

## SOLID check

Topology selection, durable state, signed contracts, relay-health policy,
browser connection ownership and opaque forwarding use separate components.
The Hub service composes these ports and remains the single application-level
authority. No worker-to-worker control path or shared-process assumption is
introduced.
