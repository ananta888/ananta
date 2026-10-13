"""Bounded scope selection, opt-in policy and actual SQL recovery on Hub ticks."""

import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services.native_result_recovery_tick import recover_native_result_on_tick
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.bpmn.test_native_result_redelivery import redelivery  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission  # noqa: F401

# ruff: noqa: F811 -- imported pytest fixtures.


def loop():
    app = Flask(__name__)
    app.config.update(ROLE="hub", ANANTA_NATIVE_RESULT_RECOVERY_ENABLED=True)
    observations = []
    return SimpleNamespace(_app=app, _stop_event=threading.Event(), tick_count=0, running=False,
                           _increment_completed=lambda: observations.append("completed"),
                           _increment_tick_count=lambda: observations.append("tick"),
                           _set_last_error=lambda value: None, _persist_state=lambda **kw: None,
                           observations=observations)


def test_tick_recovers_one_exact_task_and_does_not_dispatch(redelivery):
    state = loop()
    result = recover_native_result_on_tick(loop=state, tasks=[redelivery.task], ports_factory=lambda: redelivery.ports)
    assert result["recovered"] == result["completed"] == 1 and result["dispatched"] == 0
    assert state.observations == ["completed", "tick"]
    assert redelivery.calls == ["/api/internal/native-tasks/result"]


@pytest.mark.parametrize("denial", ["worker", "disabled", "stopped", "manual_review"])
def test_tick_policy_denials_do_not_construct_ports(redelivery, denial):
    state = loop()
    if denial == "worker":
        state._app.config["ROLE"] = "worker"
    elif denial == "disabled":
        state._app.config["ANANTA_NATIVE_RESULT_RECOVERY_ENABLED"] = False
    elif denial == "stopped":
        state._stop_event.set()
    else:
        redelivery.task.status_reason_code = "mandatory_human_review"

    constructed = []

    def forbidden():
        constructed.append(True)
        raise AssertionError("No recovery port may be constructed")

    assert recover_native_result_on_tick(loop=state, tasks=[redelivery.task], ports_factory=forbidden) is None
    assert not redelivery.calls and not constructed


@pytest.mark.parametrize("field", ["goal_id", "team_id"])
def test_changed_scope_is_not_recovered_from_stale_tick_snapshot(redelivery, field):
    state = loop()
    stale = redelivery.task.model_copy(deep=True)
    setattr(stale, field, "foreign-scope")
    assert recover_native_result_on_tick(loop=state, tasks=[stale], ports_factory=lambda: redelivery.ports) is None
    assert not redelivery.calls


def test_tick_rotation_is_bounded_and_prevents_first_task_starvation(redelivery):
    state = loop()
    second = redelivery.task.model_copy(deep=True)
    second.id = "z-synthetic-other"
    state.tick_count = 1
    # The selected second Task has no journal; never fetch the first Task instead.
    assert recover_native_result_on_tick(loop=state, tasks=[redelivery.task, second],
                                         ports_factory=lambda: redelivery.ports) is None
    assert not redelivery.calls


def test_explicit_hub_disable_overrides_environment_opt_in(redelivery, monkeypatch):
    state = loop()
    state._app.config["ANANTA_NATIVE_RESULT_RECOVERY_ENABLED"] = False
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_RECOVERY_ENABLED", "1")
    constructed = []
    assert recover_native_result_on_tick(loop=state, tasks=[redelivery.task],
                                         ports_factory=lambda: constructed.append(True)) is None
    assert not constructed
