from __future__ import annotations

import pytest

from worker.planning.planner_state import transition_state


def test_planner_state_transitions_follow_contract() -> None:
    ready = transition_state(current_state="draft", next_state="ready", trace_ref="trace:1")
    executing = transition_state(current_state=ready.state, next_state="executing", trace_ref="trace:2")
    verifying = transition_state(current_state=executing.state, next_state="verifying", trace_ref="trace:3")
    complete = transition_state(current_state=verifying.state, next_state="complete", trace_ref="trace:4")
    assert complete.state == "complete"


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(ValueError, match="planner_state_transition_invalid"):
        transition_state(current_state="draft", next_state="complete", trace_ref="trace:x")

