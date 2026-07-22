# SFU Broadcast Capacity and SLO Policy

## Scope

This policy defines the pre-admission ceilings and measurement rules for SFU broadcast fanout. The hub owns profile selection, admission, rollout, and rollback decisions. Workers may execute measurements and return artifacts, but must not select profiles, raise limits, or coordinate other workers.

The machine-readable contract is `config/sfu_broadcast_slo_profiles.json`, validated by `schemas/webrtc/sfu_broadcast_slo_profile.v1.json`. A configured number is a conservative safety ceiling, not a capacity claim. Production activation requires reproducible evidence identified by provided `RUN_*` identifiers. A missing, malformed, stale, or unknown identifier is unverified and therefore blocks activation.

## Topology Profiles

| Profile | Intended topology | Important constraint |
| --- | --- | --- |
| `single-node-direct` | One SFU node and predominantly direct ICE paths | No fleet redundancy is assumed. |
| `single-region-distributed` | Redis-coordinated nodes in one region | A room remains on one node; node count does not multiply room fanout. |
| `multi-region-distributed` | Region-aware placement across multiple SFU fleets | A room remains on one node and region failure reserve is mandatory. |
| `turn-relay-constrained` | Material TURN use or degraded ICE reachability | Lower ceilings and larger reserves account for relay bandwidth pressure. |

All profiles start with `activation_eligible=false` and empty evidence. This is intentional: values establish bounded benchmark envelopes while the feature remains fail-closed. The hub may set eligibility only after an approved run demonstrates every capacity and SLO limit for the exact topology and build.

## Admission Algorithm

For every new room, publisher, receiver, or subscription, the hub must:

1. Resolve exactly one profile from observed topology. Unknown or ambiguous topology is rejected.
2. Reject an activation-ineligible profile or a profile without at least one valid, provided `RUN_*` evidence identifier.
3. Reject if measurement windows are absent, partial, below `minimum_sample_count`, or older than the configured lookback.
4. Apply CPU, memory, egress, and subscription headroom before comparing projected load with capacity ceilings.
5. Preserve `minimum_spare_nodes` after placement. A draining or unhealthy node is not spare capacity.
6. Reject if projected utilization reaches `stop_utilization_ratio`, any reserve would be consumed, cleanup is exhausted, or a current SLO is breached.
7. Admit only through the hub-owned task and command path. Admission does not authorize a worker-to-worker handoff.

For a raw resource ceiling `C` and headroom ratio `R`, usable capacity is `floor(C * (1 - R))`. The most restrictive applicable resource decides admission. Limits must never be summed across nodes for a single room because `room_spans_nodes` is fixed to `false`.

## Statistical Decision Rules

Latency SLOs use the configured percentile over complete measurement windows. Success and delivery ratios use the same complete windows. A window is evaluable only when it has at least `minimum_sample_count` relevant observations and the configured confidence level can be reported.

`zero_sample_decision` and `partial_window_decision` are fixed to `block`. Missing data is not success. A breach becomes sustained after `consecutive_breach_windows`; however, admission is immediately blocked when a hard capacity ceiling, stop threshold, or reserve threshold is reached. Statistical aggregation must preserve topology, build, region, media kind, ICE path, and profile dimensions so a healthy cohort cannot hide a failing cohort.

The configured error allowance is an evaluation tolerance, not permission to discard failed observations. Retries and reconnects count toward both load and failure metrics.

## Trend Policy

The hub evaluates the latest `lookback_windows` and requires at least `minimum_complete_windows`. Missing windows cause `block`. The hub stops new admission when any of these conditions holds:

- utilization is at or above `stop_utilization_ratio`;
- growth exceeds `max_growth_ratio_per_window` for the evaluation window;
- utilization is at or above `warning_utilization_ratio` for `consecutive_warning_windows` and sufficient reserve cannot be demonstrated;
- the available healthy-node count would fall below `minimum_spare_nodes`.

A warning may trigger controlled drain or capacity review, but never automatic limit expansion. Raising a ceiling requires a new benchmark run, review of its stable artifact, and a versioned configuration change.

## Cleanup and Reconciliation

The hub owns periodic reconciliation. It marks disconnected participants, stale routes, and orphan rooms using their separate TTLs and deletes them in bounded batches. Each cleanup operation has a timeout and a finite attempt count. Exhaustion results in `block_and_alert`; it must not be converted into best-effort success.

Cleanup is idempotent and uses the authoritative persisted generation or route epoch. It may remove only state proven stale for that generation. A worker reports execution results to the hub and cannot independently retry forever, purge a newer generation, or schedule another worker. Admission remains blocked while stale state could cause a capacity, authorization, or accounting ambiguity.

## Reserve and Degradation

Reserve is held separately for CPU, memory, egress bandwidth, and subscriptions. All four reserves are applied before admission, not after an alert. TURN-relayed traffic uses the constrained profile whenever its resource cost is material or cannot be determined. Unknown relay health, missing fleet metrics, inconsistent room ownership, or uncertain route convergence fails closed.

During degradation, existing media may continue only within the authorization and SLO contract. The hub rejects new sessions, reconciles state, and may initiate controlled drain. It does not silently switch to a less conservative profile.

## Evidence and Change Control

An activation evidence record must bind the exact source/build snapshot, profile version, topology, test parameters, raw sample counts, percentile method, confidence calculation, cleanup outcome, trend windows, reserve calculation, and final decision. Only source identifiers supplied to the run are valid for grounded claims; identifiers must never be invented.

Profile changes are additive and reviewed independently from runtime code. Unknown fields are rejected by the schema at every level. Consumers must reject an unsupported version rather than applying defaults. Rollback restores the preceding reviewed profile and keeps admission closed until its evidence is valid for the running build and topology.

## SOLID Boundary Check

- SRP: configuration defines limits, the schema validates shape, the hub admission policy decides, and workers only measure or execute delegated cleanup.
- OCP: new topology support is introduced through a new versioned profile and adapter, without weakening existing profiles.
- LSP: an unsupported topology returns an explicit unsupported result; it is not represented by a permissive no-op profile.
- ISP: measurement, cleanup, topology detection, and admission remain focused interfaces rather than one fleet-control API.
- DIP: hub policy depends on profile, metrics, and evidence ports, not a concrete SFU implementation.

No known SOLID violation is introduced by this policy. A single service that both collects metrics, mutates profile limits, and admits sessions would violate SRP and DIP; it must be split before implementation.
