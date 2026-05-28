"""Tests für Heuristic Simulator (T06.05)."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.heuristic_simulator import (
    HeuristicSimulator,
    SimulationMetrics,
    SimulationResult,
)


def _valid_dsl(*, action_kind="follow_artifact"):
    return {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": action_kind, "confidence": 0.8},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "test", "rationale": "simulation test"},
    }


def _frames(n=10, active_panel="main"):
    return [{"screen_hash": f"hash_{i:04d}", "active_panel": active_panel} for i in range(n)]


class TestSimulationMetrics:
    def test_hit_rate_zero_frames(self):
        m = SimulationMetrics()
        assert m.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        m = SimulationMetrics(total_frames=10, hit_count=3)
        assert abs(m.hit_rate - 0.3) < 1e-6

    def test_no_action_rate(self):
        m = SimulationMetrics(total_frames=10, no_action_count=7)
        assert abs(m.no_action_rate - 0.7) < 1e-6

    def test_average_confidence_no_hits(self):
        m = SimulationMetrics(total_frames=5, hit_count=0, confidence_sum=0.0)
        assert m.average_confidence == 0.0

    def test_average_confidence(self):
        m = SimulationMetrics(hit_count=4, confidence_sum=3.2)
        assert abs(m.average_confidence - 0.8) < 1e-6

    def test_to_dict_keys(self):
        m = SimulationMetrics(total_frames=10, hit_count=5, confidence_sum=4.0)
        d = m.to_dict()
        assert "total_frames" in d
        assert "hit_count" in d
        assert "hit_rate" in d
        assert "average_confidence" in d
        assert "error_count" in d


class TestHeuristicSimulator:
    def setup_method(self):
        self.sim = HeuristicSimulator()

    def test_valid_dsl_multiple_frames_passes(self):
        dsl = _valid_dsl()
        frames = _frames(10)
        result = self.sim.simulate(dsl, frames, proposal_id="p-001", min_hit_rate=0.1)
        # DSL has no match expression → always matches → hit_rate = 1.0
        assert isinstance(result, SimulationResult)
        assert result.passed

    def test_invalid_dsl_fails_validation(self):
        dsl = {
            "dsl_version": "1.0",  # invalid
            "action": {"kind": "follow_artifact"},
            "safety": {"safety_class": "ui_motion_only"},
            "provenance": {"created_by": "test", "rationale": "x"},
        }
        frames = _frames(5)
        result = self.sim.simulate(dsl, frames, proposal_id="p-002")
        assert not result.passed
        assert result.validation_errors

    def test_empty_frames_passes_with_zero_hit_rate(self):
        dsl = _valid_dsl()
        result = self.sim.simulate(dsl, [], proposal_id="p-003", min_hit_rate=0.0)
        assert isinstance(result, SimulationResult)

    def test_min_hit_rate_threshold(self):
        dsl = _valid_dsl()
        # With no match expression DSL always hits → hit_rate = 1.0
        result = self.sim.simulate(dsl, _frames(5), min_hit_rate=0.5)
        assert result.passed

    def test_below_min_hit_rate_fails(self):
        # Create DSL with match that never matches: "not" wrapping a always-true expression
        dsl = _valid_dsl()
        dsl["match"] = {"not": {"eq": ["source_surface", "source_surface"]}}
        result = self.sim.simulate(dsl, _frames(5), proposal_id="p-004", min_hit_rate=0.5)
        assert not result.passed
        assert "hit_rate" in (result.rejection_reason or "")

    def test_simulation_result_to_dict(self):
        dsl = _valid_dsl()
        result = self.sim.simulate(dsl, _frames(3), proposal_id="p-005")
        d = result.to_dict()
        assert d["proposal_id"] == "p-005"
        assert "passed" in d
        assert "metrics" in d

    def test_metrics_total_frames_matches_input(self):
        dsl = _valid_dsl()
        frames = _frames(7)
        result = self.sim.simulate(dsl, frames, proposal_id="p-006")
        assert result.metrics.total_frames == 7

    def test_proposal_id_propagated(self):
        result = self.sim.simulate(_valid_dsl(), _frames(3), proposal_id="my-prop-id")
        assert result.proposal_id == "my-prop-id"

    def test_no_errors_in_valid_simulation(self):
        result = self.sim.simulate(_valid_dsl(), _frames(10), proposal_id="p-007")
        assert result.metrics.error_count == 0

    def test_forbidden_key_in_dsl_fails_validation(self):
        dsl = _valid_dsl()
        dsl["inline_code"] = "malicious"
        result = self.sim.simulate(dsl, _frames(3))
        assert not result.passed
        # Should fail at validator
        assert result.validation_errors or result.rejection_reason
