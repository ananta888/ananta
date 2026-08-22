from __future__ import annotations

from pathlib import Path

from scripts.check_hrm_experiments_release_gate import evaluate_repository


def test_hrm_release_gate_has_no_static_failures() -> None:
    root = Path(__file__).resolve().parents[1]
    failures = [
        item for item in evaluate_repository(root) if item["passed"] is not True
    ]
    assert failures == []
