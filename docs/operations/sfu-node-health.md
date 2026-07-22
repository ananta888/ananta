# SFU Node Health

The hub is the sole owner of node health and admission decisions. Runtime
collectors provide authenticated observations and execute active probes; a
self-reported healthy value is never an admission decision.

## Dimensions

The evaluator keeps liveness, control readiness, media readiness, admission
readiness, and drain state separate. Every dimension has a stable reason code.
Missing, stale, unverified, clock-skewed, partial, revoked, incompatible, or
unknown input blocks admission. Existing rooms are handled by drain and
failover policy rather than by the new-room admission flag.

## Named time policy

All durations are seconds:

| Setting | Default |
| --- | ---: |
| observation_ttl_seconds | 30 |
| probe_deadline_seconds | 2 |
| failure_threshold | 3 observations |
| success_threshold | 2 observations |
| flap_cooldown_seconds | 30 |
| clock_skew_seconds | 5 |

The caller persists the returned history with the node generation. A boot ID
change resets success and failure counters. Cooldown never converts an
unhealthy signal into healthy; it only delays recovery admission.

## SOLID boundary

Observation authentication and persistence, active probing, health reduction,
admission, drain, and failover are separate responsibilities. The reducer is a
pure Hub policy service over immutable inputs, which protects SRP and DIP and
makes fake-clock time series deterministic.
