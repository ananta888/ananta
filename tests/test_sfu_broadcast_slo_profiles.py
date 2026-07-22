import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sfu_broadcast_slo_profiles.json"
SCHEMA_PATH = ROOT / "schemas" / "webrtc" / "sfu_broadcast_slo_profile.v1.json"


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _set(document, path, value):
    target = document
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


def _assert_rejected(schema, config, path, value):
    candidate = copy.deepcopy(config)
    _set(candidate, path, value)
    errors = list(Draft202012Validator(schema).iter_errors(candidate))
    assert errors, f"expected {'.'.join(map(str, path))}={value!r} to be rejected"


def test_schema_and_shipped_profiles_are_valid(schema, config):
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)


def test_profile_and_topology_selection_is_unambiguous(config):
    profile_ids = [profile["id"] for profile in config["profiles"]]
    topologies = [profile["topology"] for profile in config["profiles"]]

    assert len(profile_ids) == len(set(profile_ids))
    assert len(topologies) == len(set(topologies))
    assert config["default_profile_id"] in profile_ids
    assert set(topologies) == {
        "single_node_direct",
        "single_region_distributed",
        "multi_region_distributed",
        "turn_relay_constrained",
    }


def test_unbenchmarked_profiles_are_fail_closed(config):
    for profile in config["profiles"]:
        assert profile["activation_eligible"] is False
        assert profile["evidence_run_ids"] == []
        assert profile["statistics"]["zero_sample_decision"] == "block"
        assert profile["statistics"]["partial_window_decision"] == "block"
        assert profile["trend"]["missing_window_decision"] == "block"
        assert profile["cleanup"]["on_cleanup_exhaustion"] == "block_and_alert"
        assert profile["reserve"]["reserve_breach_decision"] == "reject_new_sessions"


def test_activation_requires_a_grounded_run_identifier(schema, config):
    candidate = copy.deepcopy(config)
    candidate["profiles"][0]["activation_eligible"] = True
    assert list(Draft202012Validator(schema).iter_errors(candidate))

    candidate["profiles"][0]["evidence_run_ids"] = ["invented-evidence"]
    assert list(Draft202012Validator(schema).iter_errors(candidate))

    candidate["profiles"][0]["evidence_run_ids"] = ["RUN_supplied-benchmark.001"]
    Draft202012Validator(schema).validate(candidate)


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("profiles", 0, "capacity", "max_publishers_per_room"), 2),
        (("profiles", 0, "capacity", "max_receivers_per_room"), 0),
        (("profiles", 0, "capacity", "max_receivers_per_room"), 101),
        (("profiles", 0, "statistics", "measurement_window_seconds"), 59),
        (("profiles", 0, "statistics", "minimum_sample_count"), 99),
        (("profiles", 0, "statistics", "confidence_level"), 0.949),
        (("profiles", 0, "statistics", "allowed_error_ratio"), 0.051),
        (("profiles", 0, "statistics", "consecutive_breach_windows"), 1),
        (("profiles", 0, "cleanup", "reconciliation_interval_seconds"), 4),
        (("profiles", 0, "cleanup", "cleanup_timeout_seconds"), 61),
        (("profiles", 0, "cleanup", "cleanup_batch_size"), 0),
        (("profiles", 0, "trend", "lookback_windows"), 2),
        (("profiles", 0, "trend", "warning_utilization_ratio"), 0.81),
        (("profiles", 0, "trend", "stop_utilization_ratio"), 0.84),
        (("profiles", 0, "trend", "max_growth_ratio_per_window"), 0.251),
        (("profiles", 0, "reserve", "cpu_headroom_ratio"), 0.19),
        (("profiles", 0, "reserve", "egress_headroom_ratio"), 0.51),
        (("profiles", 0, "reserve", "minimum_spare_nodes"), 4),
        (("profiles", 0, "reserve", "apply_before_admission"), False),
    ],
)
def test_thresholds_reject_values_outside_the_contract(schema, config, path, invalid_value):
    _assert_rejected(schema, config, path, invalid_value)


@pytest.mark.parametrize(
    ("path", "boundary_value"),
    [
        (("profiles", 0, "capacity", "max_receivers_per_room"), 1),
        (("profiles", 0, "capacity", "max_receivers_per_room"), 100),
        (("profiles", 0, "statistics", "measurement_window_seconds"), 60),
        (("profiles", 0, "statistics", "minimum_sample_count"), 100),
        (("profiles", 0, "statistics", "confidence_level"), 0.95),
        (("profiles", 0, "statistics", "allowed_error_ratio"), 0.05),
        (("profiles", 0, "cleanup", "cleanup_timeout_seconds"), 1),
        (("profiles", 0, "trend", "warning_utilization_ratio"), 0.8),
        (("profiles", 0, "trend", "stop_utilization_ratio"), 0.85),
        (("profiles", 0, "reserve", "cpu_headroom_ratio"), 0.2),
        (("profiles", 0, "reserve", "cpu_headroom_ratio"), 0.5),
        (("profiles", 0, "reserve", "minimum_spare_nodes"), 0),
    ],
)
def test_contract_boundaries_are_inclusive(schema, config, path, boundary_value):
    candidate = copy.deepcopy(config)
    _set(candidate, path, boundary_value)
    Draft202012Validator(schema).validate(candidate)


def test_unknown_fields_are_rejected_at_every_contract_level(schema, config):
    for path in [
        ("unknown",),
        ("profiles", 0, "unknown"),
        ("profiles", 0, "capacity", "unknown"),
        ("profiles", 0, "slo", "unknown"),
        ("profiles", 0, "statistics", "unknown"),
        ("profiles", 0, "cleanup", "unknown"),
        ("profiles", 0, "trend", "unknown"),
        ("profiles", 0, "reserve", "unknown"),
    ]:
        _assert_rejected(schema, config, path, True)


def test_configured_profiles_preserve_conservative_invariants(config):
    profiles = {profile["id"]: profile for profile in config["profiles"]}
    direct = profiles["single-node-direct"]
    turn = profiles["turn-relay-constrained"]

    for profile in profiles.values():
        capacity = profile["capacity"]
        cleanup = profile["cleanup"]
        trend = profile["trend"]
        reserve = profile["reserve"]

        assert capacity["room_spans_nodes"] is False
        assert capacity["max_publishers_per_room"] == 1
        assert cleanup["cleanup_timeout_seconds"] < cleanup["disconnected_participant_ttl_seconds"]
        assert cleanup["reconciliation_interval_seconds"] <= cleanup["stale_route_ttl_seconds"]
        assert cleanup["stale_route_ttl_seconds"] < cleanup["orphan_room_ttl_seconds"]
        assert trend["minimum_complete_windows"] <= trend["lookback_windows"]
        assert trend["warning_utilization_ratio"] < trend["stop_utilization_ratio"]
        assert all(
            reserve[field] >= 0.2
            for field in (
                "cpu_headroom_ratio",
                "memory_headroom_ratio",
                "egress_headroom_ratio",
                "subscription_headroom_ratio",
            )
        )

    assert turn["capacity"]["max_receivers_per_room"] < direct["capacity"]["max_receivers_per_room"]
    assert turn["capacity"]["max_egress_mbps_per_node"] < direct["capacity"]["max_egress_mbps_per_node"]
    assert turn["reserve"]["egress_headroom_ratio"] > direct["reserve"]["egress_headroom_ratio"]
