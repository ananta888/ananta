from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.media_topology_policy import MediaTopologyContext, MediaTopologyPolicy


def context(**overrides):
    base = MediaTopologyContext(
        current="ordinary_direct",
        participant_count=4,
        now_ms=20_000,
        last_transition_ms=0,
        ordinary_direct_healthy=True,
        ordinary_sfu_healthy=True,
        sfu_enabled=True,
        sfu_admitted=True,
        sfu_e2ee_ready=True,
        semantic_contract_active=True,
        semantic_quality_healthy=True,
        relay_control_available=True,
    )
    return replace(base, **overrides)


def test_selects_one_semantic_bulk_path_only_after_stability_window() -> None:
    policy = MediaTopologyPolicy(cooldown_ms=5_000, semantic_stability_ms=10_000)
    early = policy.decide(context(now_ms=9_999))
    assert early.target == "ordinary_direct" and early.reason_code == "media_topology_cooldown_active"
    ready = policy.decide(context(now_ms=10_000))
    assert ready.target == "semantic_sfu" and ready.changed and ready.bulk_path_count == 1


@pytest.mark.parametrize(
    ("changes", "target"),
    [
        ({"feature_killed": True}, "ordinary_sfu"),
        ({"sfu_e2ee_ready": False}, "relay_control_only"),
        ({"sfu_admitted": False}, "relay_control_only"),
        ({"user_override": "ordinary"}, "ordinary_sfu"),
        ({"ordinary_direct_healthy": False, "ordinary_sfu_healthy": False}, "relay_control_only"),
    ],
)
def test_unknown_failure_kill_and_override_fail_to_safe_ordinary_or_control(changes, target) -> None:
    decision = MediaTopologyPolicy().decide(context(current="semantic_sfu", **changes))
    assert decision.target == target


def test_duplicate_reordered_churn_decisions_are_idempotent_and_cooldown_bound() -> None:
    policy = MediaTopologyPolicy()
    first = policy.decide(context(current="ordinary_sfu", semantic_contract_active=False, user_override="sfu"))
    second = policy.decide(context(current=first.target, semantic_contract_active=False, user_override="sfu"))
    assert first == second
    churn = policy.decide(context(current="ordinary_direct", now_ms=1_000, last_transition_ms=900))
    assert churn.target == "ordinary_direct" and churn.retry_after_ms > 0


def test_rejects_unbounded_parent_profile() -> None:
    with pytest.raises(ValueError, match="media_topology_participant_count_invalid"):
        MediaTopologyPolicy().decide(context(participant_count=250))


def test_rejects_zero_and_supports_bounded_parent_profile() -> None:
    with pytest.raises(ValueError, match="media_topology_participant_count_invalid"):
        MediaTopologyPolicy().decide(context(participant_count=0))
    assert MediaTopologyPolicy().decide(context(participant_count=8)).target in {
        "ordinary_sfu",
        "semantic_sfu",
        "relay_control_only",
    }
    with pytest.raises(ValueError, match="media_topology_participant_count_invalid"):
        MediaTopologyPolicy().decide(context(participant_count=9))


def test_group_above_mesh_limit_never_masquerades_as_direct_when_sfu_is_unavailable() -> None:
    decision = MediaTopologyPolicy(mesh_participant_limit=3).decide(
        context(
            participant_count=4,
            sfu_enabled=False,
            sfu_admitted=False,
            ordinary_sfu_healthy=False,
            relay_control_available=False,
        )
    )
    assert decision.target == "relay_control_only"
