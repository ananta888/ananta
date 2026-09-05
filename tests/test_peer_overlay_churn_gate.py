from __future__ import annotations

import json
from pathlib import Path

from scripts.run_peer_overlay_churn_gate import (
    EXPECTED_ENGINES,
    _dependency_claim,
    _measurements_complete,
)

ROOT = Path(__file__).parents[1]


def test_churn_validator_requires_real_engines_identities_and_bounded_recovery() -> None:
    bounds = {"background_tab": 2_000, "relay_failure": 2_000, "browser_crash": 2_000, "ice_restart": 5_000}

    def measurement(engine: str):
        return {
            "engine": engine,
            "deviceIdentities": [f"device-{index}" for index in range(5)],
            "processIsolation": True,
            "scenarios": {
                "backgroundTab": {"visibility": "hidden", "recoveryMs": 100},
                "relayFailure": {"recoveryMs": 100},
                "browserCrash": {"recoveryMs": 100},
                "iceRestart": {"recoveryMs": 100, "delivered": True},
            },
        }

    rows = [measurement(engine) for engine in EXPECTED_ENGINES]
    assert _measurements_complete(rows, bounds) is True
    rows[0]["scenarios"]["browserCrash"]["recoveryMs"] = 2_001
    assert _measurements_complete(rows, bounds) is False


def test_capacity_dependencies_must_be_hub_registered_test_evidence() -> None:
    report = {
        "status": "passed",
        "repository_revision": "a" * 40,
        "evidence": {
            "issuer": "hub-evidence-registry",
            "run_id": "RUN_real",
            "source_id": "SRC_real",
            "scope": "test",
            "synthetic": True,
            "production_release_eligible": False,
        },
    }
    assert _dependency_claim(report)["valid_test_evidence"] is True
    report["evidence"]["run_id"] = "invented"
    assert _dependency_claim(report)["valid_test_evidence"] is False


def test_committed_churn_gate_is_test_only_and_references_every_capacity_run() -> None:
    report = json.loads(
        (ROOT / "artifacts/test-gates/peer-overlay-churn.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "passed"
    assert report["decision"] == "test_gate_passed_production_no_go"
    assert report["evidence"]["scope"] == "test"
    assert report["evidence"]["production_release_eligible"] is False
    assert {row["engine"] for row in report["measurements"]} == EXPECTED_ENGINES
    assert set(report["capacity_claims"]) == {
        "four_peer_mesh",
        "five_peer_overlay",
        "nat_turn_matrix",
    }
    for claim in report["capacity_claims"].values():
        assert claim["run_id"].startswith("RUN_")
        assert claim["source_id"].startswith("SRC_")
        assert claim["production_release_eligible"] is False
