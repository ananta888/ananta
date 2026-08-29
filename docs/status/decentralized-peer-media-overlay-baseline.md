# Decentralized Peer Media Overlay Baseline

Review date: 2026-08-29

Repository basis: `main@7617afa2e`

Runtime/source evidence: no assignment-bound `SRC_*` or `RUN_*` references were provided.

## Decision summary

The Hub remains the only topology, membership and route authority. The current
production-safe media paths remain direct 1:1 WebRTC and LiveKit SFU with their
existing E2EE gates. A peer-routed opaque **data** overlay is a bounded
implementation target. Cross-`RTCPeerConnection` forwarding of encoded RTP
media without decode/re-encode is `no_go` as a portable production foundation
(`DG-01`), so Peer-DAG media must remain disabled unless a later standards and
registered browser gate changes that decision.

The decisive standards constraint is explicit: WebRTC Encoded Transform binds
an encoded frame to its sender or receiver owner and the write algorithm drops
a frame whose owner differs; a processor cannot move frames between streams.
The specification is still a Working Draft. RFC 9605 defines SFrame encryption,
but it does not grant a browser application a portable cross-connection RTP
forwarding primitive. WebCodecs is therefore only a separate bounded experiment,
not an equivalent continuation of the existing RTP pipeline.

Primary review sources:

- <https://www.w3.org/TR/webrtc-encoded-transform/>
- <https://datatracker.ietf.org/doc/html/rfc9605>
- <https://www.w3.org/TR/webcodecs/>
- <https://www.w3.org/TR/webrtc-stats/>

## Capability inventory

| Capability | State | Concrete implementation and boundary |
| --- | --- | --- |
| Direct 1:1 session | present | `WebrtcSessionService` owns exactly one `RTCPeerConnection`; signaling, generation fencing and bounded queues are production paths. |
| Direct public audio/video E2EE | present | Hub security contract and peer identity bind `PairMediaE2eeCoordinatorService`; `PairMediaE2eeTransformAdapter` installs non-extractable keys into encoded transforms. Failure degrades to data-only or terminates, never plaintext media. |
| Generic encoded-frame crypto | partial | `MediaE2eeTransformService` binds publication, sender, recipient scope, codec, kind and epoch with AEAD and a bounded replay window. Its `ANME` framing is not RFC-9605 SFrame. |
| Group/SFU content keys | present | Hub-signed `GroupKeyEpochAuthorization` carries only metadata and key-package references; `WebrtcGroupKeyService` verifies it and stores non-extractable browser keys. LiveKit/SFU stays the group-media fallback. |
| Membership/key/route/topology epochs | partial | SFU contracts and repositories fence these epochs; there is no canonical peer-overlay epoch aggregate or signed peer membership event. |
| Priority/backpressure | present for existing data paths | `WebrtcPrioritySendQueue` and SFU data queue policy use bounded per-class queues and observable drops. They do not yet implement per-child multi-hop relay queues. |
| Multi-peer mesh manager | missing | No service owns an isolated connection per remote peer. `ordinary_mesh` is currently only a policy label. |
| Resource-adaptive mesh admission | missing | `MediaTopologyPolicy` has a configured static limit. It does not consume trustworthy per-device browser measurements. |
| Safe mesh fallback | defective | With more participants than the mesh limit and no admitted SFU, `_ordinary_fallback` can return `ordinary_direct`, falsely presenting a non-group 1:1 path as the bulk group path. |
| Signed peer route lease/link ticket | missing | Existing SFU route intents are not authority for peer-to-peer edges and must not be reused as if they were. |
| Publication-specific Peer-DAG | missing | No Hub DAG builder, peer link lease, primary/backup parent or route failover exists. |
| Opaque multi-hop data relay | missing | Secure envelopes, chunk digests and relay services are reusable pieces, but no client-to-client two-hop route execution exists. |
| Cross-peer encoded RTP relay | no_go | Current WebRTC Encoded Transform ownership rules do not provide the required portable frame transfer between separate peer connections. |
| WebCodecs media relay | test_only/missing | No prototype or registered browser evidence exists; congestion control, synchronization and rendering parity remain unverified. |
| Per-edge ICE/TURN | partial | The current single direct edge receives short-lived credentials and bounded degradation; there is no independent policy per overlay edge. |
| IP privacy and relay consent | partial | Direct-neighbor and TURN behavior exist, but no separate revocable peer-relay consent controls an overlay edge. |
| Content-free observability | present for SFU, missing for overlay | Existing SFU read models redact content and credentials. No Peer-DAG publication view exists. |
| Browser/NAT/churn/security evidence | missing | Local unit and SFU gates are not assignment-bound Peer-DAG `RUN_*` evidence and must not be promoted. |

## E2EE path matrix

| Path | Key source | Transform | Sender / receiver | Safe fallback |
| --- | --- | --- | --- | --- |
| Public direct pair media | pair security handshake derived from Hub-bound peer identities and contract | `PairMediaE2eeFrameCodec` through `PairMediaE2eeTransformAdapter` | one local sender to one authenticated peer | data-only connection or terminal fail-closed state |
| Generic direct encoded media | caller-supplied non-extractable AES-GCM key | `MediaE2eeTransformService` | publication/sender/recipient-scope bound | unsupported transform is a denial; no plaintext claim |
| SFU group media | client-managed content key under Hub-signed epoch authorization | `SfuMediaFrameCryptoService` / LiveKit E2EE lifecycle | publication sender to authorized receiver group | ordinary direct/mesh only when actually executable; otherwise control-only |
| Peer-DAG media | none | none | none | LiveKit E2EE; Peer-DAG media remains disabled |

## SOLID boundary assessment

`WebrtcSessionService` preserves a large existing responsibility surface:
signaling, one connection, DataChannel parsing, queueing, media publication,
E2EE activation, ICE and audit. Expanding that class into a connection graph
would violate SRP and OCP. The overlay implementation must introduce small
ports for link lifecycle, route leases, relay queues, crypto and observations;
the existing 1:1 service remains behind a compatibility adapter.

The existing SFU ports and pair E2EE coordinator are reusable abstractions, but
their infrastructure state must not be shared implicitly with a peer overlay.
Unsupported adapter capabilities must return a bounded denial.

## Release boundary

The static audit can establish architecture facts and the standards `no_go`
decision. It cannot establish Chromium/Firefox capacity, NAT reachability,
battery cost, malicious-relay behavior or production recovery timing. Those
claims remain blocked until authorized, reproducible and assignment-bound
`RUN_*` evidence exists. No source or run identifier is synthesized here.
