"""The stale-proposing sweep must respect a task's own propose timeout."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.routes.tasks import autopilot_stale_task_recovery as rec


def _recovery(app_cfg):
    loop = SimpleNamespace(_app=None, _agent_config=lambda: dict(app_cfg))
    return rec.StaleTaskRecovery(
        loop=loop,
        append_trace_event=lambda *args, **kwargs: None,
        update_local_task_status=lambda *args, **kwargs: None,
        approval_lifecycle=None,
        now_ts=1000.0,
    )


@pytest.fixture
def fixed_timeout(monkeypatch):
    monkeypatch.setattr(
        "agent.services.propose_runtime_policy.resolve_propose_llm_timeout_seconds",
        lambda *, effective_config, task_kind: int(effective_config["task_propose_timeout_seconds"]),
    )


def test_the_proposing_threshold_tracks_the_propose_timeout(fixed_timeout):
    recovery = _recovery({"task_propose_timeout_seconds": 600})
    task = SimpleNamespace(task_kind="development", goal_id="")
    assert recovery._proposing_stale_seconds(task) == 600 + rec.PROPOSING_STALE_MARGIN_SECONDS


def test_the_proposing_threshold_is_floored_and_capped(fixed_timeout):
    # The floor is the Hub's governed propose forward deadline (240s default)
    # plus the margin, never shorter, so an in-flight forward is not pre-empted.
    assert _recovery({"task_propose_timeout_seconds": 5})._proposing_stale_seconds(
        SimpleNamespace(task_kind="x", goal_id="")) == 240 + rec.PROPOSING_STALE_MARGIN_SECONDS
    assert _recovery({"task_propose_timeout_seconds": 100000})._proposing_stale_seconds(
        SimpleNamespace(task_kind="x", goal_id="")) == rec.PROPOSING_STALE_MAX_SECONDS


def test_the_proposing_threshold_tracks_the_governed_forward_deadline(fixed_timeout):
    """A short model timeout must not let the sweep cut an in-flight forward.

    The security policy waits up to ``propose_timeout`` (here 600s) for the
    forward; the sweep must not reset the task before that forward can have
    finished, even though the raw model budget is only 120s.
    """
    recovery = _recovery(
        {
            "task_propose_timeout_seconds": 120,
            "autopilot_security_level": "balanced",
            "autopilot_security_policies": {"balanced": {"propose_timeout": 600}},
        }
    )
    task = SimpleNamespace(task_kind="development", goal_id="")
    assert recovery._proposing_stale_seconds(task) == 600 + rec.PROPOSING_STALE_MARGIN_SECONDS


def test_a_long_propose_is_not_reset_but_a_stuck_one_is(fixed_timeout):
    recovery = _recovery({"task_propose_timeout_seconds": 600})
    resets: list = []
    events: list = []
    recovery.update_local_task_status = lambda task_id, status, **kwargs: resets.append((task_id, status))
    recovery.append_trace_event = lambda task_id, event, **kwargs: events.append((task_id, event, kwargs))

    still_proposing = SimpleNamespace(id="t1", status="proposing", task_kind="development", goal_id="",
                                      updated_at=1000.0 - 500, last_output=None)
    recovery.reset_stale_proposing([still_proposing])
    assert resets == [] and events == []

    stuck = SimpleNamespace(id="t2", status="proposing", task_kind="development", goal_id="",
                            updated_at=1000.0 - 700, last_output=None)
    recovery.reset_stale_proposing([stuck])
    assert resets == [("t2", "todo")]
    assert events == [("t2", "stale_proposing_reset", {"reason": "no_output_after_660s"})]


def test_a_manual_override_puts_the_repair_sweeps_on_hold():
    """``retry`` drives the task for 600s; the sweeps must not fight that.

    The dispatch gate already refuses to dispatch while an override is active, so a sweep that
    retries and then fails the task for standing still killed the retry 68s after ``retry`` --
    three historical stale retries were already spent, and the override never got a chance.
    """
    recovery = _recovery({"task_propose_timeout_seconds": 600})
    resets: list = []
    events: list = []
    recovery.update_local_task_status = lambda task_id, status, **kwargs: resets.append((task_id, status))
    recovery.append_trace_event = lambda task_id, event, **kwargs: events.append((task_id, event))

    driven = SimpleNamespace(
        id="t1", status="assigned", goal_id="", task_kind="coding",
        updated_at=1000.0 - 600, manual_override_until=1000.0 + 300,
        verification_status={}, last_output=None,
    )
    recovery.retry_stale_active([driven])
    assert resets == [] and events == []

    # once the override has expired the task is recovered as before
    driven.manual_override_until = 1000.0 - 1
    recovery.retry_stale_active([driven])
    assert resets == [("t1", "todo")]


def test_a_stale_propose_of_a_driven_task_is_not_reset(fixed_timeout):
    recovery = _recovery({"task_propose_timeout_seconds": 600})
    resets: list = []
    recovery.update_local_task_status = lambda task_id, status, **kwargs: resets.append((task_id, status))
    recovery.append_trace_event = lambda *args, **kwargs: None

    driven = SimpleNamespace(
        id="t2", status="proposing", goal_id="", task_kind="coding",
        updated_at=1000.0 - 700, manual_override_until=1000.0 + 300, last_output=None,
    )
    recovery.reset_stale_proposing([driven])
    assert resets == []

    driven.manual_override_until = 0
    recovery.reset_stale_proposing([driven])
    assert resets == [("t2", "todo")]
