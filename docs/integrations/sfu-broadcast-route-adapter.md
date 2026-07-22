# SFU broadcast route adapter

## Boundary

`LiveKitBroadcastRouteAdapter` implements the four vendor-neutral route ports
after a Hub-owned command authorizer has verified the signed route intent. It
uses only the pinned `LiveKitControlApiClient` RoomService surface. It neither
compiles audiences nor changes receiver, publication, layer, TTL, epoch, or
fencing values.

The adapter depends on focused ports for:

- exact Hub command and domain-binding authorization
- TLS/control-endpoint identity verification
- durable operation reservation and result persistence
- independent observation authorization
- the public LiveKit control client

The operation ledger binds the idempotency key, canonical command digest,
route version, audience digest, epochs, TTL, and fencing token before the first
vendor call. A conflicting operation ID fails closed.

## Acknowledgement semantics

A successful `UpdateSubscriptions` response proves only that the authenticated
API accepted the command. The route-port result is therefore
`unknown/route_control_api_accepted_unverified`, never an Ananta ACK. Promotion
to active requires a separate authoritative reconciliation observation.

The currently pinned public LiveKit client exposes participant presence but no
exact authoritative `RouteProjectionV1` observation. Consequently the adapter
reports `supported=false` and `route_observation_unsupported`. Bootstrap and
rollout policy must keep the feature disabled until a real runtime capability
supplies the same observation contract. Hub memory or a successful HTTP status
must not be used to emulate that capability.

## Reconciliation

`SfuFanoutRouteReconciliationService` is Hub-owned and runs under an exclusive
lease. It processes a paginated revoke phase before the ensure phase. Every
candidate is re-authorized after observation and before mutation using the
lease fence. Revoked, tombstoned, expired, stale-epoch, and stale-parent routes
are removed before an absent current route may be applied.

The run is bounded by item, page, wall-clock, and lease budgets. A checkpoint
is persisted after every candidate. Unknown observations defer apply/update;
they do not widen access. Authority changes to revoke during recovery take
precedence immediately.

The persistence adapters, bootstrap wiring, real-container evidence, and
runtime capability evidence are separate follow-up work. No `SRC_*` or `RUN_*`
identifier is asserted by this implementation.
