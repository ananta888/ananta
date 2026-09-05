# ADR: Reject MLS adoption for the current peer overlay

- Status: accepted
- Date: 2026-09-05
- Decision gate: DG-02

## Decision

Ananta does not adopt RFC 9420 MLS for the current peer overlay. The bounded
data overlay continues to use Hub-signed membership events, short-lived route
leases and client-managed publication keys. This is a rejection, not a partial
MLS implementation; no TreeKEM, MLS credential or commit state is introduced.

MLS solves group authenticated key establishment, but it would add a second
membership/commit state machine beside the Hub-owned membership authority.
Ananta would first need a formally specified binding from Keycloak identity to
Hub device identity and MLS credentials, deterministic treatment of concurrent
commits, recovery semantics across Hub failover, and an SFrame exporter path.
None is necessary for the current default-off opaque data DAG, while a partial
mapping would weaken the single-control-plane invariant.

## Migration boundary

A future bounded pilot requires a new ADR and Hub-registered interoperability,
fork, multi-device and recovery evidence. It must be an additive key adapter;
the Hub remains the canonical membership and policy owner. Until then, unknown
MLS messages and credentials are unsupported and fail closed. Existing group
key authorization remains the complete selected path.
