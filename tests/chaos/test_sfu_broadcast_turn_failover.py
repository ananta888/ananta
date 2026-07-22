from pathlib import Path

from scripts.sfu_broadcast_advanced_gate import load_advanced_gate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/test-profiles/sfu-broadcast/turn-chaos.json"


def test_turn_profile_is_pool_based_and_turn_only():
    profile = load_advanced_gate_profile(PROFILE)
    topology = profile.document["topology"]

    assert topology["turn_instance_count"] >= 2
    assert topology["relay_policy"] == "turn_only"
    assert topology["allocation_limit_enforced"] is True
    assert topology["port_limit_enforced"] is True
    assert topology["bandwidth_limit_enforced"] is True


def test_turn_profile_requires_lifecycle_and_pool_faults():
    profile = load_advanced_gate_profile(PROFILE)
    scenarios = set(profile.document["required_scenarios"])

    assert {"credential_rotation", "credential_revoke", "credential_lifetime_expiry"} <= scenarios
    assert {"turn_pool_instance_kill", "dns_failure", "tls_failure", "sfu_failover"} <= scenarios
    assert "no_e2ee_downgrade" in profile.document["required_assertions"]
