# ADR: optional SFU for ordinary semantic-media fan-out

Status: accepted, opt-in and fallback-gated (2026-07-19)

## Context and decision

Ananta needs one publisher upload for multi-receiver ordinary audio/video,
while preserving Hub-owned admission, epoch fencing, E2EE and the existing
peer-to-peer fallback. We select **LiveKit Server 1.13.1** with the browser SDK
**livekit-client 2.20.1**, pinned by container digest and exact npm version.
The deployment stays disabled unless its Compose profile and Hub feature flag
are both enabled.

The candidates were:

| Candidate | License/integration | Security and media capabilities | Operational consequence |
| --- | --- | --- | --- |
| LiveKit | Apache-2.0; maintained server and JS SDK | Room-scoped JWT grants, E2EE client support, simulcast/SVC | Selected: smallest maintained integration surface; Ananta still supplies membership epochs, short TTL and rekey policy |
| mediasoup | ISC; low-level, signaling-agnostic Node/C++ library | Simulcast/SVC and detailed RTP control | Rejected for this increment: Ananta would have to build and audit the signaling, room, token and operational layer |
| Janus | GPL-3.0; native plugin gateway | Mature WebRTC plugins and media routing | Rejected for this increment: plugin/native operations and licensing add more deployment and integration work |

LiveKit's self-hosted mode keeps media infrastructure under operator control,
but it is a single-home SFU design, not transparent multi-node media failover.
Its self-hosted token revocation is not treated as instantaneous: Ananta binds
tokens to a membership epoch, uses a maximum 60-second TTL, stops reissuing on
revocation and rotates E2EE keys before accepting more protected frames.

## Security and fallback boundaries

- The Hub is the sole issuer and validates tenant, room, participant, role,
  publication/subscription IDs, permission subset, membership epoch and
  optimistic revision before signing.
- The SFU routes packets but never grants Lease, workflow or Worker authority.
- Strict rooms require browser insertable-stream/E2EE support. Unsupported
  clients are visibly downgraded only when policy permits ordinary WebRTC;
  strict policy rejects the join.
- Ordinary media, semantic control and private recovery artifacts retain
  separate authorization and queues. The SFU cannot make a private recovery
  publication generally subscribable.
- Unknown capability, unhealthy SFU, stale epoch, failed E2EE, quality breach
  or explicit user choice selects ordinary WebRTC. Hysteresis prevents a
  second simultaneous bulk path.

## Evidence and upgrade rule

The repository contains deterministic policy/unit gates and an opt-in real
three-peer browser spike. A release claim requires the live spike against the
pinned image, two receivers receiving the same publication, one publisher
PeerConnection/upload, ciphertext-only E2EE capture evidence, and successful
fallback after SFU termination. Missing live evidence is a release-gate
failure, never a synthetic pass.

Upgrades require a new digest, SDK/server compatibility review, repeated
three-peer/E2EE/fallback gates and an updated ADR evidence record.

The destructive failover gate additionally boots the productive Hub SFU and
semantic-compute blueprints against an isolated SQL database. It verifies Hub
SIGKILL/restart state continuity, a fenced and replaced primary lease, a
single-winner validator CAS conflict, and ordinary-audio availability during
the outage. A browser-side authority simulator is not acceptable evidence.

Primary references: [LiveKit repository](https://github.com/livekit/livekit),
[self-hosting](https://docs.livekit.io/transport/self-hosting/),
[token grants](https://docs.livekit.io/home/server/generating-tokens),
[authentication](https://docs.livekit.io/home/concepts/authentication/),
[E2EE](https://docs.livekit.io/transport/encryption/),
[JS SDK 2.20.1 reference](https://docs.livekit.io/reference/client-sdk-js/hierarchy.html),
[mediasoup repository](https://github.com/versatica/mediasoup), and
[Janus repository](https://github.com/meetecho/janus-gateway).
