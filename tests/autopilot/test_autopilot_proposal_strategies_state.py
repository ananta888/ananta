"""A valid proposal must not inherit the task's accumulated strategy failure state.

``verification_status.autopilot_strategy`` (attempt_count, failed_models, failed_temperatures,
failed_sources, reason_code) is written only on failure. It survives a later success, so the
next evaluation starts pre-exhausted -- live, a run that produced a valid executable proposal
still ended in ``failed / autopilot_strategy_exhausted``.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.routes.tasks.autopilot_proposal_strategies import _clear_stale_strategy_state


def test_a_valid_proposal_clears_the_accumulated_strategy_state():
    writes: list = []
    events: list = []
    task = SimpleNamespace(
        id="t1",
        status="failed",
        verification_status={
            "autopilot_strategy": {
                "attempt_count": 7,
                "failed_models": ["ananta-code-27b"],
                "failed_temperatures": [0.2],
                "failed_sources": ["local"],
                "reason_code": "autopilot_strategy_exhausted",
                "last_failed_at": 123.0,
                "repair_rounds": 0,
            },
            "other_marker": {"kept": True},
        },
    )

    cleared = _clear_stale_strategy_state(
        task,
        update_local_task_status=lambda *args, **kwargs: writes.append((args, kwargs)),
        append_trace_event=lambda *args, **kwargs: events.append((args, kwargs)),
    )

    assert cleared is True
    args, kwargs = writes[0]
    assert args == ("t1", "failed")  # the status itself is not touched
    assert kwargs["verification_status"] == {"other_marker": {"kept": True}}
    assert kwargs["verification_status"].get("autopilot_strategy") is None
    assert kwargs["event_type"] == "autopilot_strategy_state_cleared"
    assert kwargs["force"] is True
    assert "reason_code" in kwargs["event_details"]["cleared_keys"]
    assert task.verification_status == {"other_marker": {"kept": True}}  # in-memory too
    assert events[0][0][1] == "autopilot_strategy_state_cleared"


def test_nothing_is_written_when_the_state_is_already_clean():
    writes: list = []
    events: list = []
    task = SimpleNamespace(id="t2", status="proposing", verification_status={"other": 1})

    cleared = _clear_stale_strategy_state(
        task,
        update_local_task_status=lambda *args, **kwargs: writes.append(1),
        append_trace_event=lambda *args, **kwargs: events.append(1),
    )

    assert cleared is False
    assert writes == [] and events == []
    assert task.verification_status == {"other": 1}


def test_the_state_is_dropped_even_when_it_was_the_only_key():
    writes: list = []
    task = SimpleNamespace(
        id="t3",
        status="todo",
        verification_status={"autopilot_strategy": {"attempt_count": 3, "failed_models": []}},
    )

    cleared = _clear_stale_strategy_state(
        task,
        update_local_task_status=lambda *args, **kwargs: writes.append(kwargs),
        append_trace_event=lambda *args, **kwargs: None,
    )

    assert cleared is True
    assert writes[0]["verification_status"] == {}
    # empty values are not reported as cleared keys
    assert writes[0]["event_details"]["cleared_keys"] == ["attempt_count"]
    assert task.verification_status == {}
