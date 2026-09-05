from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.run_peer_nat_matrix_gate import EXPECTED_ENGINES, _matrix_complete, _route_epoch_proof

ROOT = Path(__file__).parents[1]


def test_route_switch_preserves_membership_and_key_epochs_and_rejects_stale_lease() -> None:
    assert _route_epoch_proof() == {
        "route_epoch_advanced": True,
        "membership_epoch_preserved": True,
        "key_epoch_preserved": True,
        "stale_route_rejected": True,
    }


def test_matrix_validator_requires_each_transport_and_bounded_failure() -> None:
    def measurement(engine: str) -> dict[str, object]:
        return {
            "engine": engine,
            "scenarios": {
                "turnUdp": {"candidateType": "relay", "relayProtocol": "udp"},
                "turnTcp": {"candidateType": "relay", "relayProtocol": "tcp"},
                "networkSwitch": {
                    "before": {"relayProtocol": "udp"},
                    "after": {"relayProtocol": "tcp"},
                    "connectionGenerationAdvance": 1,
                },
                "blockedTurn": {"outcome": "turn_unreachable_bounded"},
            },
        }

    complete = [measurement(engine) for engine in sorted(EXPECTED_ENGINES)]
    assert _matrix_complete(complete) is True
    assert _matrix_complete(complete[:1]) is False
    broken = json.loads(json.dumps(complete))
    broken[0]["scenarios"]["blockedTurn"]["outcome"] = "retrying"  # type: ignore[index]
    assert _matrix_complete(broken) is False


def test_committed_nat_matrix_is_redacted_test_evidence() -> None:
    report_path = ROOT / "artifacts/test-gates/peer-nat-matrix.json"
    raw = report_path.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["status"] == "passed"
    assert report["cleanup_verified"] is True
    assert report["evidence"]["scope"] == "test"
    assert report["evidence"]["synthetic"] is True
    assert report["evidence"]["production_release_eligible"] is False
    assert {row["engine"] for row in report["measurements"]} == EXPECTED_ENGINES
    assert "credential" not in raw.lower()
    assert "username" not in raw.lower()
    assert re.search(r"(?<![A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9])", raw) is None
