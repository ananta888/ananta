from __future__ import annotations

from scripts.run_geomap_release_gate import run_gate


def test_release_gate_is_offline_headless_and_complete() -> None:
    report = run_gate()
    assert report["status"] == "passed"
    assert report["network_policy"] == "denied"
    assert report["human_intervention_required"] is False
    assert len(report["checks"]) == 10
