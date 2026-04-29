from __future__ import annotations

from worker.planning.replan_policy import should_replan


def test_replan_triggers_are_profile_aware_and_bounded() -> None:
    allowed = should_replan(trigger="verification_failure", attempts_used=0, max_attempts=2, profile="balanced")
    exhausted = should_replan(trigger="verification_failure", attempts_used=2, max_attempts=2, profile="balanced")
    assert allowed["replan"] is True
    assert exhausted["replan"] is False
    assert exhausted["reason"] == "replan_budget_exhausted"

