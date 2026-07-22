# Distributed SFU runtime

## Ownership boundary

The Hub owns flags, admission, capacity reservations, cluster selection, reconciliation and every background-job lease. Runtime agents only execute authenticated commands whose fencing token is newer than their persisted token. They do not schedule work, select another worker, or exchange tasks with another runtime.

In `livekit_native`, LiveKit owns concrete room-to-node placement. Hub records a concrete node only after an authenticated observation. In `hub_cluster_only`, Hub selects one persistent cluster target; node placement inside that target remains the runtime's responsibility. Neither mode claims Hub-directed room sharding.

## Fail-closed defaults

The committed directory has two targets per mode but sets admission false. The Redis TLS directory intentionally contains no certificates or ACL secrets. The committed LiveKit native config contains a non-working password placeholder. Missing or stale flag ACK, parent readiness, capacity, route control, runtime health, image/config digest, source evidence or run evidence blocks new admission.

`docker-compose.semantic-media.yml` remains a single-node compatibility profile and is not eligible for the distributed gate.

## Profiles

- `livekit_native_distributed` starts two pinned LiveKit processes and the isolated LiveKit-only Redis scope.
- `hub_cluster_only_distributed` starts two independently addressable authenticated runtime targets.
- `authenticated_runtime_extension` remains the single-target compatibility profile and cannot satisfy the distributed gate.

Provision distinct runtime certificates, TPM handles and TPM devices for extension targets. Provision Redis server/CA certificates and a least-privilege ACL outside Git. Render an operator-owned LiveKit config containing the Redis credential and point `ANANTA_SFU_LIVEKIT_NATIVE_CONFIG_FILE` at it.

Redis AOF is enabled with `everysec`; backup and recovery are operator-owned. Restore into an isolated instance, validate the LiveKit-only DB and key scope, then admit nodes behind a new monotonically higher directory and projection fence. Redis must not be used for Ananta application state.

## Verification

The structural verifier emits content-free JSON and never creates evidence identifiers:

```bash
python scripts/verify_sfu_distributed_runtime.py
```

It keeps `multi_node`, `distributed_capacity` and `rolling_drain` false until both a supplied `SRC_*` and `RUN_*` identifier validate and every structural check passes. A real gate must additionally prove startup, rolling drain, partition, Redis or control outage, runtime loss, rejoin and incompatible image/config digest behavior.

The Redis image is intentionally reported as unpinned until an approved registry digest is supplied. No release claim may bypass that reason code.

## Rollout and rollback

1. Keep all broadcast flags false and run migrations.
2. Enroll both targets and confirm fresh authenticated observations.
3. Project the same flag/cohort version to both targets and wait for matching ACKs.
4. Enable a bounded cohort only after parent readiness and capacity gates pass.
5. Drain a target before upgrade; never rewrite a lower fencing token.
6. On failure, set `immediate_security_fence`, stop new admission, drain, then roll back the image or config.

Rollback never deletes open admission operations, active capacity reservations, pending projections or scheduler leases. The migration downgrade refuses to remove open sagas.
