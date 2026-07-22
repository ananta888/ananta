# WebRTC/SFU broadcast boundaries

This document applies the accepted follow-up ADR to concrete data flows. It is
additive to the Parent semantic-media architecture. The Hub remains the control
plane and task owner; Workers execute one delegated task and never orchestrate.

## Control flow

```text
Browser intent
  -> Hub authentication / membership / admission
  -> Hub Audience + membership/route/topology epoch + CAS revision
  -> selected runtime port
       livekit_control_api | authenticated_runtime_extension | unsupported
  -> SFU desired route projection
  -> Hub reconciliation and audit
```

In `livekit_native`, Hub cluster/region eligibility stops before node placement:
LiveKit alone selects the concrete node. Ananta stores desired state and its own
epochs, not a fabricated LiveKit node lease.

## Media and key flow

```text
Browser publisher plaintext
  -> canonical client key derivation / LiveKit BaseKeyProvider
  -> encrypted frame
  -> SFU and optionally TURN (opaque)
  -> authorized receiver browser
  -> canonical client decryption

Hub -> signed Parent key-epoch and per-member key-package references -> Browser
Hub -X-> content key
SFU/TURN/Database/Observability/Worker -X-> content key or media plaintext
```

The current custom `SfuMediaFrameCryptoService` is a test-only helper, not the
productive cryptographic boundary. Productive SFU E2EE is the
`BaseKeyProvider` path in
`frontend-angular/src/app/services/livekit-sfu-room.adapter.ts`.

## Audience projection

The Hub persists the authoritative Audience. A publisher receives only a
bounded projection and applies default-deny LiveKit
`setTrackSubscriptionPermissions`. This publisher-side projection is an
enforcement layer, not a transfer of Audience ownership. Receivers also apply
their narrowed subscription grant. Either side must deny unknown publication,
receiver, epoch or revision.

The current safety limits are explicit:

- 8 participants per room/group;
- 7 receivers per publication;
- 7 targeted data destinations.

They are hard limits, not a capacity or release promise.

## Component boundaries

| Component/container | Owns | Reads | Emits | Must not do |
| --- | --- | --- | --- | --- |
| Browser | Capture/render, content keys, local permission projection | User media, Hub grants, Parent key-epoch refs | Encrypted frames/data, authenticated intent | Create global Audience or tasks |
| Hub | Membership, admission, Audience, cluster/region policy, epochs, fencing, failover, queue | Authenticated intent, durable state, sanitized health | Narrow grants, desired routes, delegated tasks | Process media or choose a native LiveKit node |
| LiveKit SFU | Native room/node placement and packet forwarding | Narrow route operations, opaque packets | Opaque packets, native health/routing metadata | Business policy, task/Worker orchestration |
| TURN | Relay allocation and opaque packet forwarding | Relay credentials and encrypted packets | Encrypted packets, bounded health | Infer or mutate Audience |
| Database | Durable Hub control state and audit metadata | Hub repository writes | Transactional state/CAS results | Store content keys/media or run policy |
| Observability | Allowlisted operational evidence | Sanitized metrics/events | Aggregates and alerts | Collect media, keys, tokens, SDP or ICE secrets |
| Hub-delegated Worker | One bounded execution step | Explicit task context/artifact grants | Result and evidence to Hub | Call another Worker, mutate Audience, control SFU |
| Egress, future/optional | One consented export | Purpose-bound delegated stream/artifact | Bounded export artifact | Ambient recording or autonomous scheduling |

Each runs in its own container when deployed. No design may rely on process
globals, a shared filesystem or an implicit network attachment.

## Epoch and fencing model

| Epoch/revision | Authority | Invalidated by | Enforcement |
| --- | --- | --- | --- |
| Membership epoch | Hub | join, leave, revoke, Hub failover | Token/grant validation and client key authorization |
| Route epoch | Hub | Audience or route replacement | Hub CAS, serialized desired-state command and reconciliation |
| Topology epoch | Hub | region/cluster/failover change | Admission fencing and replacement workflow |
| LiveKit native placement | LiveKit | native drain/failure/rebalance | LiveKit runtime; observed by health/reconciliation only |

Stock `livekit_control_api` does not acquire stronger Ananta fencing merely
because the Hub sends an epoch. The Hub sends absolute desired subscriptions,
serializes commands per room and measures bounded convergence. Strong runtime
acknowledgement is valid only in `authenticated_runtime_extension` after its
protocol is selected and proven. Otherwise the operation is `unsupported`.

## Failover

1. The Hub marks the current topology unhealthy and stops renewing grants.
2. The Hub advances topology/route state transactionally and fences stale Hub work.
3. For `livekit_native`, the Hub chooses an eligible replacement cluster/region; LiveKit chooses its node.
4. Browsers obtain fresh admission and Parent key-epoch references as required.
5. The Hub reconciles absolute Audience projection and audits convergence.
6. A delegated Worker may verify evidence but cannot select, command or repair another Worker/SFU.

No SFU-to-Worker, TURN-to-Worker, Egress-to-Worker or Worker-to-Worker task path
is permitted. All work returns through the Hub queue.

## Observability boundary

Allowed examples are bounded counts, latency histograms, route/topology epoch,
reason code, cluster/region identifier, packet/drop totals and TURN utilization.
Disallowed examples are frame/data payload, content key or package material,
JWT/TURN credentials, raw SDP/ICE candidates and unconstrained participant
identity. Content-free telemetry still exposes timing, IP and traffic-shape
metadata; access, retention and aggregation must reflect that residual risk.

## Current readiness

The baseline proves a Pair/small-group foundation and a pinned single-node
LiveKit container. It does not prove broadcast capacity, a Redis-backed fleet,
an authenticated node agent or regional TURN. Parent readiness remains
`no_go/observe_only`; canary and release therefore remain fail-closed while
default-false audit, contract and feasibility work proceeds.

## SOLID boundary check

- SRP is protected by separating Hub policy, runtime adaptation, persistence, telemetry and Egress.
- DIP/OCP are protected by the closed runtime port and additive implementations.
- LSP requires explicit `unsupported`; a no-op success adapter is forbidden.
- ISP requires separate route, health and Egress capabilities rather than a broad SFU god interface.
- Hidden side effects are prohibited: every route mutation is Hub-authorized, versioned and auditable.
