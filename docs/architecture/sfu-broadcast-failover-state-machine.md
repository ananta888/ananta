# SFU Broadcast Failover State Machine

The hub is the only failover decision maker. Runtime adapters revoke, mint, and
apply the exact bounded commands delegated to them. They never choose another
node or increment route, topology, fencing, or key epochs.

## Ordering

The state machine starts planned. It first revokes old admission, optionally
waits for the canonical parent rekey port, then issues a short-lived token for
the target scope and activates the new route. Completion requires an exact
runtime acknowledgement for the target binding and all epochs.

Route epoch, topology epoch, and fencing strictly increase. The service never
increments key epoch. If rekey is required, only a signed parent result with a
larger key epoch is accepted. In LiveKit-native mode the target is room,
cluster, and region; a preselected node identity is invalid.

## Bounded failure

failover_rto_seconds, retry_budget, retry_cooldown_seconds, and token_ttl_seconds
are explicit policy values. Exhausted retries, RTO, missing target, kill switch,
or authorization revocation terminates in parent_fallback. A failed fallback
terminates controlled_failed. No state can create an unbounded reconnect loop.

Every reconciliation step checks kill switch and revocation again. Stale
bindings, tokens, route acknowledgements, fencing, or smaller route, topology,
or key epochs cannot complete the decision.

## SOLID boundary

Decision persistence, old-admission revocation, parent rekey, token issuance,
route activation, guards, and fallback are narrow ports. The Hub service owns
only ordering and policy. This protects SRP, ISP, and DIP while preserving the
hub-worker architecture.
