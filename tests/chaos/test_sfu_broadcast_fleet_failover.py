from pathlib import Path

from scripts.sfu_broadcast_advanced_gate import load_advanced_gate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/test-profiles/sfu-broadcast/fleet-chaos.json"


def test_fleet_profile_covers_split_brain_and_version_skew():
    profile = load_advanced_gate_profile(PROFILE)
    scenarios = set(profile.document["required_scenarios"])

    assert profile.document["topology"]["hub_count"] >= 2
    assert profile.document["topology"]["sfu_runtime_count"] >= 2
    assert profile.document["topology"]["room_count"] >= 2
    assert {"split_brain", "rolling_version_skew", "redis_cluster_failure", "database_failover"} <= scenarios
    assert "no_worker_to_worker_orchestration" in profile.document["required_assertions"]


def test_fleet_partition_cannot_grant_or_renew_routes():
    profile = load_advanced_gate_profile(PROFILE)
    thresholds = {item["metric"]: item for item in profile.document["thresholds"]}

    assert thresholds["partition_route_grants"]["value"] == 0
    assert thresholds["partition_renewals"]["value"] == 0
