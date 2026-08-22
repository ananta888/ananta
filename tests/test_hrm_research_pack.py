from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hrm_research_pack_gate_is_deterministic_and_fail_closed() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_hrm_research_pack.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "archived_todo": True,
        "closed_contract_count": 10,
        "hub_grounding": "verified_promoted",
        "item_count": 32,
        "live_runtime": "not_claimed",
        "pending_promotion_decisions": 0,
        "prepared_decisions": 32,
        "schema": "ananta.hrm-research-gate.v1",
        "status": "passed",
        "sudoku_fixture": "valid",
        "threat_count": 12,
    }
