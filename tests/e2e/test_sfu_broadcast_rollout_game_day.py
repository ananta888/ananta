from pathlib import Path

from scripts.sfu_broadcast_advanced_gate import load_advanced_gate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/test-profiles/sfu-broadcast/rollout-game-day.json"


def test_rollout_profile_is_ordered_and_unknown_holds_advancement():
    profile = load_advanced_gate_profile(PROFILE)
    topology = profile.document["topology"]

    assert topology["rollout_stages"] == ["flag_off", "internal", "cohort", "percent", "released"]
    assert topology["unknown_evidence_policy"] == "hold"
    assert topology["legacy_path_required"] is True


def test_rollout_profile_covers_rollback_and_compatibility_invariants():
    profile = load_advanced_gate_profile(PROFILE)

    assert {"rollback", "legacy_compatibility", "credential_revoke"} <= set(profile.document["required_scenarios"])
    assert {"no_destructive_deletion", "no_right_widening"} <= set(profile.document["required_assertions"])
