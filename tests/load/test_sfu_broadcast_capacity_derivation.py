from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.derive_sfu_broadcast_capacity_profiles import (
    DEFAULT_MAXIMUM_VARIANCE_PERCENT,
    EvidenceError,
    VerifiedMeasurement,
    apply_conservative_reserve,
    contiguous_safe_tier,
    derive_candidate_from_verified_measurements,
    derive_capacity_profiles,
)


def _measurement(
    transport_mode: str,
    receiver_tier: int,
    *,
    passed: bool = True,
    variance_percent: float = 2.0,
) -> VerifiedMeasurement:
    """Build arithmetic-only input, not a production evidence record."""

    return VerifiedMeasurement(
        transport_mode=transport_mode,
        receiver_tier=receiver_tier,
        passed=passed,
        complete=True,
        repetitions=3,
        variance_percent=variance_percent,
        thresholds_passed=passed,
        resource_limits_observed=True,
        security_controls_preserved=True,
        soak_stable=True,
    )


def _capacity_measurements(highest_tier: int) -> list[VerifiedMeasurement]:
    tiers = [tier for tier in (10, 25, 50, 100, 250) if tier <= highest_tier]
    return [
        *[_measurement("direct", tier) for tier in tiers],
        *[_measurement("all_turn", tier) for tier in tiers],
        _measurement("direct_pair", 2),
    ]


def test_first_failed_tier_stops_even_when_a_higher_tier_passes() -> None:
    measurements = [
        _measurement("direct", 10),
        _measurement("direct", 25),
        _measurement("direct", 50, passed=False),
        _measurement("direct", 100),
    ]

    assert contiguous_safe_tier(
        measurements,
        transport_mode="direct",
        maximum_variance_percent=DEFAULT_MAXIMUM_VARIANCE_PERCENT,
    ) == 25


def test_missing_tier_stops_before_a_higher_passing_tier() -> None:
    measurements = [
        _measurement("all_turn", 10),
        _measurement("all_turn", 50),
    ]

    assert contiguous_safe_tier(
        measurements,
        transport_mode="all_turn",
        maximum_variance_percent=DEFAULT_MAXIMUM_VARIANCE_PERCENT,
    ) == 10


def test_excessive_variance_stops_the_tier_sequence() -> None:
    measurements = [
        _measurement("direct", 10),
        _measurement("direct", 25, variance_percent=15.01),
        _measurement("direct", 50),
    ]

    assert contiguous_safe_tier(
        measurements,
        transport_mode="direct",
        maximum_variance_percent=15.0,
    ) == 10


def test_reserve_is_monotone_and_never_rounds_up() -> None:
    for tier in (10, 25, 50, 100, 250):
        capacities = [
            apply_conservative_reserve(tier, reserve)
            for reserve in (20.0, 25.0, 30.0, 40.0)
        ]
        assert capacities == sorted(capacities, reverse=True)
        assert all(capacity <= tier for capacity in capacities)


def test_more_contiguous_safe_tiers_cannot_reduce_capacity() -> None:
    lower = derive_candidate_from_verified_measurements(
        topology="single-region",
        infrastructure_profile="bounded-fixture",
        measurements=_capacity_measurements(25),
        reserve_percent=20.0,
        maximum_variance_percent=15.0,
    )
    higher = derive_candidate_from_verified_measurements(
        topology="single-region",
        infrastructure_profile="bounded-fixture",
        measurements=_capacity_measurements(50),
        reserve_percent=20.0,
        maximum_variance_percent=15.0,
    )

    assert higher["limits"]["admitted_receivers"] >= lower["limits"][
        "admitted_receivers"
    ]


def test_direct_pair_is_required_but_never_drives_capacity() -> None:
    base = _capacity_measurements(50)
    small_pair = derive_candidate_from_verified_measurements(
        topology="single-region",
        infrastructure_profile="bounded-fixture",
        measurements=base,
        reserve_percent=20.0,
        maximum_variance_percent=15.0,
    )
    large_pair_input = [m for m in base if m.transport_mode != "direct_pair"]
    large_pair_input.append(_measurement("direct_pair", 250))
    large_pair = derive_candidate_from_verified_measurements(
        topology="single-region",
        infrastructure_profile="bounded-fixture",
        measurements=large_pair_input,
        reserve_percent=20.0,
        maximum_variance_percent=15.0,
    )

    assert large_pair["limits"] == small_pair["limits"]
    assert large_pair["derivation"]["direct_pair_used_for_capacity"] is False


def test_failed_direct_pair_regression_blocks_candidate() -> None:
    measurements = [
        *[m for m in _capacity_measurements(25) if m.transport_mode != "direct_pair"],
        _measurement("direct_pair", 2, passed=False),
    ]

    with pytest.raises(EvidenceError, match="direct_pair_regression_failed"):
        derive_candidate_from_verified_measurements(
            topology="single-region",
            infrastructure_profile="bounded-fixture",
            measurements=measurements,
            reserve_percent=20.0,
            maximum_variance_percent=15.0,
        )


def test_fixture_bundle_cannot_become_production_capacity() -> None:
    result = derive_capacity_profiles(
        {
            "schema": "ananta.sfu-broadcast-capacity-evidence-bundle.v1",
            "test_fixture": True,
            "environments": [],
        }
    )

    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert result["blocking_reasons"] == ["non_production_evidence_rejected"]


def test_production_controls_cannot_remove_the_minimum_reserve() -> None:
    result = derive_capacity_profiles(
        {
            "schema": "ananta.sfu-broadcast-capacity-evidence-bundle.v1",
            "environments": [],
        },
        reserve_percent=19.99,
    )

    assert result["status"] == "blocked"
    assert result["blocking_reasons"] == ["production_reserve_below_minimum"]


def test_checked_in_outputs_are_honestly_blocked() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    catalog = json.loads(
        (repository_root / "config/generated/sfu_broadcast_capacity_profiles.json")
        .read_text(encoding="utf-8")
    )
    report = json.loads(
        (
            repository_root
            / "artifacts/test-gates/sfu-broadcast-capacity-derivation.json"
        ).read_text(encoding="utf-8")
    )

    assert catalog["status"] == "blocked"
    assert catalog["candidates"] == []
    assert catalog["activation"]["performed"] is False
    assert report["status"] == "blocked"
    assert report["release_blocking"] is True
    assert report["claims"]["capacity_profiles_activated"] is False
