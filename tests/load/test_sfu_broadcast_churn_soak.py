from pathlib import Path

from scripts.sfu_broadcast_advanced_gate import build_plan, evaluate_external_result, load_advanced_gate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/test-profiles/sfu-broadcast/soak.json"


def test_soak_profile_enforces_duration_churn_and_topology_minimums():
    profile = load_advanced_gate_profile(PROFILE)

    assert profile.execution["measurement_seconds"] >= 7200
    assert profile.document["topology"]["rooms_min"] >= 3
    assert profile.document["topology"]["receivers_per_room_min"] >= 10
    assert profile.document["topology"]["join_leave_events_per_minute_min"] >= 2
    assert profile.document["topology"]["rekeys_per_room_per_hour_min"] >= 4
    assert profile.document["topology"]["layer_flaps_per_minute_min"] >= 5


def test_soak_plan_is_external_and_deterministic():
    profile = load_advanced_gate_profile(PROFILE)
    digests = {name: "a" * 64 for name in profile.document["environment"]["required_digest_names"]}

    assert build_plan(profile, digests=digests) == build_plan(profile, digests=digests)


def test_soak_fixture_cannot_be_accepted_as_real_evidence():
    profile = load_advanced_gate_profile(PROFILE)
    reasons = evaluate_external_result(
        {"schema": profile.external_result_schema, "test_fixture": True},
        profile=profile,
        expected_digests={name: "a" * 64 for name in profile.document["environment"]["required_digest_names"]},
    )

    assert reasons
