from __future__ import annotations

import pytest

from agent.services.peer_overlay_quality_policy import PeerOverlayQualityPolicy


def observation(observer: str, **changes) -> dict[str, object]:
    value: dict[str, object] = {
        "validation": "hub-quality-observation-accepted-v1",
        "observer_peer_id": observer,
        "relay_peer_id": "relay-1",
        "route_epoch": 4,
        "observed_at_ms": 100_000,
        "sample_count": 10,
        "link_state": "good",
        "relay_delivery_ratio": 0.99,
        "end_to_end_delay_ms": 100,
    }
    value.update(changes)
    return value


def test_single_peer_cannot_degrade_global_quality() -> None:
    result = PeerOverlayQualityPolicy().aggregate(
        relay_peer_id="relay-1",
        route_epoch=4,
        observations=[observation("peer-1", relay_delivery_ratio=0.1, end_to_end_delay_ms=9_000)],
        now_ms=100_000,
    )
    assert result["reason_code"] == "insufficient_quorum"
    assert result["relay_state"] == "unknown"


def test_quorum_separates_link_relay_and_end_to_end_quality_without_pii() -> None:
    result = PeerOverlayQualityPolicy().aggregate(
        relay_peer_id="relay-1",
        route_epoch=4,
        observations=[
            observation("peer-1", relay_delivery_ratio=0.7, end_to_end_delay_ms=2_000),
            observation("peer-2", relay_delivery_ratio=0.8, end_to_end_delay_ms=2_500),
            observation("peer-3", link_state="failed", relay_delivery_ratio=0.99, end_to_end_delay_ms=100),
        ],
        now_ms=100_000,
    )
    assert result["link_state"] == "good"
    assert result["relay_state"] == "degraded"
    assert result["end_to_end_state"] == "degraded"
    assert result["delivery_bucket"] == "poor"
    assert result["contains_pii"] is False
    assert not any("peer-" in str(value) for value in result.values())


def test_unverified_unknown_or_oversized_observations_fail_closed() -> None:
    policy = PeerOverlayQualityPolicy(maximum_observations=2)
    with pytest.raises(ValueError, match="unverified"):
        policy.aggregate(
            relay_peer_id="relay-1",
            route_epoch=4,
            observations=[observation("peer-1", validation="caller-asserted")],
            now_ms=100_000,
        )
    with pytest.raises(TypeError):
        policy.aggregate(
            relay_peer_id="relay-1",
            route_epoch=4,
            observations=[{**observation("peer-1"), "ip_address": "192.0.2.1"}],
            now_ms=100_000,
        )
    with pytest.raises(ValueError, match="budget"):
        policy.aggregate(
            relay_peer_id="relay-1",
            route_epoch=4,
            observations=[observation(f"peer-{index}") for index in range(3)],
            now_ms=100_000,
        )
