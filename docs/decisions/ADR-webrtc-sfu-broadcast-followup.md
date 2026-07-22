# ADR: WebRTC/SFU broadcast follow-up ownership and runtime boundary

Status: accepted for flag-off implementation; activation blocked (2026-07-22)

## Context

The Parent semantic-media program established Pair media, Hub admission,
membership epochs, group-key authorization and a bounded LiveKit path. Broadcast
fan-out extends that foundation without transferring control-plane ownership to
the SFU, TURN, Egress or a Worker. Parent readiness is currently `no_go` with
rollout stage `observe_only`; this ADR therefore authorizes architecture and
default-false implementation only.

## Decision

The Hub remains the sole orchestration and policy authority. It owns:

- membership and admission;
- authoritative Audience and its projection;
- eligible SFU cluster and region selection;
- membership, route and topology epochs;
- compare-and-set updates, fencing and reconciliation;
- failover policy and task delegation;
- the task queue and every Worker assignment.

For placement mode `livekit_native`, the Hub selects only an eligible
cluster/region and issues bounded desired state. Concrete node choice is made
exclusively by LiveKit. Ananta must not shadow LiveKit's node scheduler or claim
a node acknowledgement that the selected public API does not provide.

The Hub signs and authorizes Parent key-epoch references and member-bound key
package references only. Content-key generation, derivation and installation
remain in the canonical browser crypto path. The Hub, SFU, TURN, database,
Observability and ordinary Workers must never receive a content key.

The SFU may process only:

- Hub-authorized route operations supported by the selected runtime mode;
- opaque encrypted media/data packets;
- native local resource limits;
- health and routing metadata needed for Hub policy.

The SFU may not evaluate tenant membership, business Audience, consent,
workflow policy, Worker selection or task fan-out. It may not orchestrate a
Worker. Any duplicate implementation of those Hub responsibilities is an
architecture error.

## Closed runtime modes

Exactly one of these modes is selected per deployment capability record:

| Mode | Contract | Fencing semantics |
| --- | --- | --- |
| `livekit_control_api` | Stock, authenticated public LiveKit control APIs only | Hub serializes absolute desired state and reconciles bounded eventual convergence; no invented node ACK or Ananta epoch CAS at the SFU. |
| `authenticated_runtime_extension` | Separately built and authenticated extension with a narrow, versioned port | May accept route/topology epoch and fencing token only after capability and conformance evidence. It still cannot own policy or orchestration. |
| `unsupported` | Required operation is unavailable or unverified | Fail closed; keep broadcast flags off and use an authorized existing fallback. |

The set is closed. A no-op adapter must not report success for an unsupported
operation because that would violate substitutability and hide stale access.

## Container and trust boundaries

| Boundary | Plaintext / secrets allowed | Accepted input | Explicitly forbidden |
| --- | --- | --- | --- |
| Browser container/process | User-authorized media, content keys, ephemeral identity credentials | Hub grants, Parent key-epoch references, opaque remote frames | Task orchestration, peer-authored policy |
| Hub container | Membership/Audience metadata, signing credentials, desired routes and epochs | Authenticated client intent, SFU/TURN health, Worker result | Media/content keys, concrete LiveKit-native node selection |
| SFU container | No application plaintext or content key | Narrow grants, opaque packets, native limits | Membership policy, task queue, Worker calls |
| TURN container | No application plaintext or content key | Authenticated relay allocations and opaque packets | Audience decisions, route authority |
| Database container | Hub control state, epochs, CAS revisions, audit metadata | Hub repository operations | Media payload, content key, independent scheduling |
| Observability backend | Allowlisted metadata and bounded identifiers | Sanitized Hub/SFU/TURN metrics and audit events | Media, keys, tokens, SDP/ICE secrets, unbounded identities |
| Hub-delegated Worker container | One bounded task context and explicitly released artifacts | Hub task queue assignment | Membership/Audience mutation, worker-to-worker delegation, SFU control |
| Egress container, if enabled later | Plaintext only under explicit consent and purpose-bound grant | One Hub-delegated export job | Ambient room access, autonomous recording, orchestration |

Containers share no implicit state. Cross-boundary calls are authenticated,
least-privilege, observable and reproducible through explicit configuration.

## Ownership matrix against the Parent program

Every shared responsibility has exactly one Parent owner. The follow-up consumes
the contract and must not reimplement it.

| Shared responsibility | Sole Parent task | Follow-up rule |
| --- | --- | --- |
| Repository-grounded Pair/WebRTC/Relay/SFU inventory | `ASMP-BASE-001` | Extend with broadcast-specific findings; do not fork the Parent baseline. |
| Browser/Hub/SFU/Worker trust model and canonical client crypto ownership | `ASMP-BASE-002` | Add Audience, TURN, Fleet and Egress boundaries only. |
| Existing SFU join, rekey, fallback/failover and small-group load basis | `ASMP-SFU-010` | Treat it as a prerequisite, never as a broadcast-capacity claim. |

No responsibility is mapped to more than one ASMP task. If implementation would
duplicate a row, the change must be rejected or reduced to an adapter over the
Parent-owned contract.

## Failure and failover behavior

The Hub advances the relevant route/topology epoch before replacing desired
state, stops grant renewal, reconciles absolute subscriptions and rotates the
authorized key epoch when membership or revocation requires it. A stale Worker,
SFU response or route command cannot create Hub state. In
`livekit_control_api`, convergence is measured and bounded rather than described
as strong server-side CAS. In `unsupported`, the operation fails closed.

## Consequences and SOLID rationale

- SRP: policy stays in Hub services; protocol adapters translate only one runtime boundary.
- OCP/DIP: new runtimes implement a narrow port selected by capability evidence.
- LSP: unsupported capabilities return `unsupported`, never synthetic success.
- ISP: health, route projection and Egress use separate focused contracts.
- Testability: epoch, fencing and reconciliation policies can be exercised without a live SFU; runtime conformance remains an external gate.

The deliberate limitation is weaker fencing in stock-control mode. Adding a
larger god adapter or moving policy into an SFU/node agent would violate SRP and
the hub-worker architecture and is rejected.
