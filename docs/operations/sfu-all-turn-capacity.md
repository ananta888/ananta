# SFU all-TURN worst-case capacity gate

`config/test-profiles/sfu-broadcast/all-turn-worst-case.json` is a test
contract, not evidence that the product can force SFU media through TURN.  Its
default state is `no_go`; an operator must first establish the two independent
capabilities `livekit_external_turn` and `sfu_all_turn_media_path` with real
`SRC_*` references.

## Required execution

Run every receiver load for every scenario in the matrix.  Each run uses one
publisher, forces the publisher and every receiver to relay-only candidates,
and records the selected candidate pair plus positive relay bytes for every
media leg.  UDP, TCP and TLS, symmetric NAT/CGNAT, loss/jitter/bandwidth
constraints, credential lifetimes, allocation/permission/channel expiry,
process restart and pool failover are mandatory.  A direct, host or server-
reflexive candidate makes the sample fail rather than reducing its score.

Each scenario/load combination uses the declared warm-up, duration, fixed
seed, repeat count and cleanup timeout.  Keep the raw runner output immutable;
the report references it by `sha256:<64 lowercase hex>` and uses only IDs that
the evidence registry actually issued.  Do not manufacture `SRC_*` or `RUN_*`
identifiers.

## Gate input

The hub-side `SfuAllTurnCapacityGate` consumes the profile and a report with:

- `profile_id`, `source_refs`, `run_refs`, `artifact_sha256`
- `configured_admission_receiver_limit`
- one `scenario_results` entry per scenario/load
- exactly the configured repeat count per entry
- per repeat: `stable_receivers`, `join_success_ratio`,
  `packet_loss_percent`, `p95_jitter_ms`, `nonrelay_candidate_count`,
  `publisher_relay_bytes`, `receiver_legs_with_relay_bytes`, and
  `cleanup_complete`, plus TURN/SFU CPU and memory percentages and TURN relay
  bandwidth utilization

The evaluator requires 30 percent bandwidth/CPU headroom and 25 percent memory
headroom, takes the lowest passing capacity across all scenarios, applies the
35 percent reserve, and rejects an admission limit above that result.
Missing evidence, scenarios, repeats, relay bytes, cleanup, or stable capacity
always yields `no_go`.

## Current status

No real source or run IDs are present in the checked-in profile, and the
artifact digest is `null`.  Therefore the all-TURN path must remain disabled
and its capacity contribution is zero.
