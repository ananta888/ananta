import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]


def test_all_turn_worst_case_profile_matches_strict_schema():
    schema = json.loads(
        (ROOT / "schemas/testing/sfu_all_turn_scenario_profile.v1.json").read_text()
    )
    profile = json.loads(
        (ROOT / "config/test-profiles/sfu-broadcast/all-turn-worst-case.json").read_text()
    )

    jsonschema.Draft202012Validator(schema).validate(profile)
    assert profile["activation_status"] == "no_go"
    assert profile["evidence"] == {"source_refs": [], "run_refs": [], "artifact_sha256": None}

