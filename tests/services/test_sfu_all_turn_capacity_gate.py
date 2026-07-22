import json
from pathlib import Path

from agent.services.sfu_all_turn_capacity_gate import SfuAllTurnCapacityGate


ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_profile_is_no_go_without_grounded_evidence():
    profile = json.loads(
        (ROOT / "config/test-profiles/sfu-broadcast/all-turn-worst-case.json").read_text()
    )

    result = SfuAllTurnCapacityGate().evaluate(
        profile,
        {
            "profile_id": profile["profile_id"],
            "source_refs": [],
            "run_refs": [],
            "artifact_sha256": "",
            "configured_admission_receiver_limit": 0,
            "scenario_results": [],
        },
    )

    assert result.status == "no_go"
    assert result.safe_receiver_limit == 0
    assert "all_turn_source_evidence_missing" in result.reason_codes
    assert "all_turn_run_evidence_missing" in result.reason_codes

