from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts/domain/semantic-sfu-three-peer.json"


def test_committed_three_peer_evidence_is_fail_closed_and_pinned():
    assert ARTIFACT.exists(), "real SFU spike artifact missing"
    report = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert report["schema"] == "ananta.semantic-sfu-three-peer-spike.v1"
    assert report["pinned"] == {
        "server_version": "1.13.1",
        "server_digest": "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3",
        "client_version": "2.20.1",
    }
    assert report["topology"] == {"publishers": 1, "receivers": 2, "expected_publisher_uploads": 1}
    assert report["verdict"] in {"pass", "fail"}
    assert report["engines"], "unavailable live evidence cannot pass"
    if report["verdict"] == "pass":
        assert all(row["verdict"] == "pass" for row in report["engines"])


@pytest.mark.skipif(os.environ.get("RUN_LIVE_SFU_TESTS") != "1", reason="requires pinned live SFU")
def test_live_three_peer_spike():
    subprocess.run(
        ["node", "scripts/spikes/semantic_sfu_three_peer.mjs"], cwd=ROOT,
        check=True, timeout=180, env=os.environ.copy(),
    )
