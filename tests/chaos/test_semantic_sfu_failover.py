from __future__ import annotations

from dataclasses import replace

from agent.services.media_topology_policy import MediaTopologyContext, MediaTopologyPolicy


def active() -> MediaTopologyContext:
    return MediaTopologyContext(
        current="semantic_sfu",
        participant_count=4,
        now_ms=50_000,
        last_transition_ms=40_000,
        ordinary_direct_healthy=True,
        ordinary_sfu_healthy=True,
        sfu_enabled=True,
        sfu_admitted=True,
        sfu_e2ee_ready=True,
        semantic_contract_active=True,
        semantic_quality_healthy=True,
        relay_control_available=True,
    )


def test_sfu_failure_immediately_falls_back_and_rejoin_waits_for_fresh_admission_and_hysteresis() -> None:
    policy = MediaTopologyPolicy(cooldown_ms=5_000, semantic_stability_ms=10_000)
    failed = policy.decide(replace(active(), ordinary_sfu_healthy=False, sfu_e2ee_ready=False))
    assert failed.target == "relay_control_only" and failed.changed

    stale_rejoin = policy.decide(
        replace(
            active(),
            current="ordinary_direct",
            now_ms=52_000,
            last_transition_ms=50_000,
            sfu_admitted=False,
            sfu_e2ee_ready=False,
        )
    )
    assert stale_rejoin.target == "ordinary_direct"

    admitted_but_cooling = policy.decide(
        replace(
            active(),
            current="ordinary_direct",
            now_ms=59_999,
            last_transition_ms=50_000,
        )
    )
    assert admitted_but_cooling.target == "ordinary_direct"
    assert admitted_but_cooling.reason_code == "media_topology_cooldown_active"

    fresh_epoch_ready = policy.decide(
        replace(
            active(),
            current="ordinary_direct",
            now_ms=60_000,
            last_transition_ms=50_000,
        )
    )
    assert fresh_epoch_ready.target == "semantic_sfu" and fresh_epoch_ready.changed


def test_feature_kill_and_revocation_never_leave_two_bulk_paths_active() -> None:
    decision = MediaTopologyPolicy().decide(replace(active(), feature_killed=True, sfu_admitted=False))
    assert decision.target == "relay_control_only"
    assert decision.bulk_path_count == 1
