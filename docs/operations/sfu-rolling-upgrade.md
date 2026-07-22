# SFU Rolling Upgrade

The hub owns the drain queue, compatibility decision, deadlines, concurrency,
and room policy. Runtime adapters only execute idempotent admission and room
commands carrying the hub fencing value.

## State machine

The supported sequence is requested, admission_stopped, draining, then
drained. A deadline moves the flow to forced through the parent fallback.
Cancellation is explicit and starts a cooldown. Admission must stop before any
room is held, rejoined, or sent to parent fallback.

The version matrix is exact and fail-closed across contract, adapter, E2EE, and
route versions. Wildcards and implicit downgrade are not supported.

The default policy allows one concurrent drain per cluster, a 300 second
deadline, and a 60 second cooldown. Raising parallelism requires measured
rolling-upgrade evidence. Stable control and transcript paths plus stale
route/token revocation are mandatory room-adapter acknowledgements.

## Recovery

Every effect receives a deterministic operation identifier and fencing value.
CAS conflicts are returned to the hub for bounded reconciliation. Workers and
runtime nodes do not select another node or start their own migration loop.

## SOLID boundary

Compatibility, state persistence, admission effects, and room effects use
separate interfaces. The service coordinates policy but contains no LiveKit or
container implementation, protecting SRP, ISP, and DIP.
