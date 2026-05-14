from __future__ import annotations

from agent.services.execution_improvement_loop_service import ExecutionImprovementLoopService


def test_improvement_loop_valid_transition_and_max_loop_enforcement() -> None:
    svc = ExecutionImprovementLoopService()
    ok = svc.transition(from_state="verify", to_state="critique", attempt_index=1, max_loops=3)
    blocked = svc.transition(from_state="repair", to_state="execute", attempt_index=3, max_loops=3)
    assert ok.allowed is True
    assert blocked.allowed is False
    assert blocked.reason == "max_improvement_loops_reached"


def test_build_verification_critique_contains_missing_paths() -> None:
    svc = ExecutionImprovementLoopService()
    critique = svc.build_verification_critique(
        expected_artifacts=[{"relative_path": "backend"}, {"relative_path": "frontend"}],
        verification={"reason": "missing_expected_artifacts"},
        observed_artifacts=[{"workspace_relative_path": "backend"}],
        logs="failed check",
    )
    assert critique["schema"] == "verification_critique.v1"
    assert "frontend" in critique["missing_paths"]
    assert critique["failed_reasons"]

