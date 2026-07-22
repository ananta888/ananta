from pathlib import Path

from scripts.sfu_broadcast_advanced_gate import evaluate_external_result, load_advanced_gate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/test-profiles/sfu-broadcast/scale.json"


def test_scale_profile_uses_required_receiver_tiers_and_real_browser_sentinels():
    profile = load_advanced_gate_profile(PROFILE)
    topology = profile.document["topology"]

    assert topology["receiver_tiers"] == [10, 25, 50, 100, 250]
    assert topology["real_browser_sentinels_min"] >= 3
    assert topology["approval_stops_after_first_failure"] is True
    assert {"camera_direct", "camera_all_turn", "semantic_broadcast", "private_recovery"} <= set(
        topology["required_modes"]
    )


def test_scale_shape_only_or_fixture_evidence_cannot_pass():
    profile = load_advanced_gate_profile(PROFILE)
    reasons = evaluate_external_result(
        {"schema": profile.external_result_schema, "test_fixture": True},
        profile=profile,
        expected_digests={name: "b" * 64 for name in profile.document["environment"]["required_digest_names"]},
    )

    assert reasons
