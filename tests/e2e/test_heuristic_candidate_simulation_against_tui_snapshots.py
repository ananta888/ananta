"""E2E: Simulation bewertet DSL-Kandidaten gegen TUI-Snapshots.

T08.03: Gültiger DSL-Kandidat wird gegen Snapshot-Fixture simuliert.
"""
import pytest
from agent.services.heuristic_runtime.heuristic_simulator import HeuristicSimulator

_GOOD_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "match": {"eq": ["source_surface", "tui_snake"]},
    "action": {"kind": "follow_artifact", "confidence": 0.8},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "test", "rationale": "Good heuristic"},
}

_JITTER_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "match": {"eq": ["source_surface", "NEVER_MATCHES"]},
    "action": {"kind": "no_action", "confidence": 0.1},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "test", "rationale": "Bad heuristic"},
}

_INVALID_DSL = {
    "dsl_version": "1.0",  # falsche Version
    "action": {"kind": "follow_artifact"},
}


def _make_frames(n: int) -> list[dict]:
    return [
        {"frame_id": f"f{i}", "screen_hash": f"hash_{i}", "active_panel": "BODY",
         "source_surface": "tui_snake", "width": 120, "height": 32}
        for i in range(n)
    ]


def test_good_heuristic_passes_simulation():
    sim = HeuristicSimulator()
    frames = _make_frames(10)
    result = sim.simulate(_GOOD_DSL, frames, proposal_id="good_p", min_hit_rate=0.1)
    assert result.passed
    assert result.metrics.hit_rate > 0.0


def test_jitter_heuristic_gets_low_score():
    sim = HeuristicSimulator()
    frames = _make_frames(10)
    result = sim.simulate(_JITTER_DSL, frames, proposal_id="jitter_p", min_hit_rate=0.5)
    assert not result.passed


def test_invalid_candidate_is_rejected():
    sim = HeuristicSimulator()
    frames = _make_frames(5)
    result = sim.simulate(_INVALID_DSL, frames, proposal_id="invalid_p")
    assert not result.passed
    assert result.rejection_reason is not None


def test_simulation_produces_metric_dict():
    sim = HeuristicSimulator()
    frames = _make_frames(5)
    result = sim.simulate(_GOOD_DSL, frames, proposal_id="metrics_p")
    d = result.to_dict()
    assert "hit_rate" in d["metrics"]
    assert "total_frames" in d["metrics"]
    assert d["metrics"]["total_frames"] == 5
