# Peer overlay responsibility and port boundaries

The Hub remains the only authority for membership, topology, route leases,
link tickets, epochs and release stages. Browser peers execute the closed
projection they receive; they do not elect routes or broaden capabilities.

| Responsibility | Owner | Focused boundary |
| --- | --- | --- |
| Membership and epoch mutation | Hub | `PeerOverlayControlService` and `MembershipEventV1` |
| Publication DAG construction | Hub | `PeerOverlayTopologyService` |
| Durable revisions and ticket replay | Hub infrastructure | `PeerOverlayStateStore` |
| Relay-health decision | Hub policy | `PeerOverlayRelayHealthPolicy` |
| One browser link | Browser adapter | `PeerLinkSession` focused lifecycle, publication, data and observation ports |
| Small mesh composition | Browser | `MultiPeerConnectionManager` |
| Opaque multi-hop forwarding | Browser data plane | `PeerOverlayDataRelay` |
| Data-class budgets and priority | Browser data policy | `peer-overlay-traffic-policy` |
| Authenticated media-layer narrowing | Direct/LiveKit media adapter | `AuthenticatedLayerSelection` |
| Content-free multi-observer quality | Hub policy | `PeerOverlayQualityPolicy` |
| Parent-path activation and failover fencing | Browser data plane | `PeerOverlayParentFailover` |
| Edge-scoped SDP/ICE transport selection | Browser signaling adapter | `PeerOverlayLinkSignaling` |
| Encoded-frame cryptography | Browser crypto adapter | `MediaFrameCryptoPort` and scoped key leases |
| Product release | Hub | `PeerOverlayReleaseGate` |

`WebrtcSessionService` remains the compatibility facade for the established
one-to-one path. It must not absorb group topology, route policy, multi-peer
state, overlay forwarding or release decisions. LiveKit remains behind its
existing focused room-session ports. New overlay code may depend on closed
contracts and injected ports, but never on SDK-specific LiveKit types or the
one-to-one facade.

This preserves SRP by assigning one reason to change to each component, OCP by
adding adapters instead of modifying the direct/SFU core, ISP through focused
browser ports, and DIP through injected factories and crypto capabilities.
Unsupported capabilities return bounded errors; they are never successful
no-ops.

The data relay authenticates immutable origin fields and keeps the changing
hop/path envelope inside Hub-leased authenticated links. Destination routes
map an end peer to an authorized immediate child; a relay cannot add a child.
Every relay independently enforces the route epoch, hop/path bounds, replay
budget and queue caps. Parent failover consumes a Hub-validated successor
command, keeps backup bulk disabled before activation, and restores the primary
if the bounded switch cannot complete. Link signaling consumes one short-lived
Hub ticket per offer and automatically uses the Hub rendezvous path when the
existing DataChannel is absent or races with a partition.
