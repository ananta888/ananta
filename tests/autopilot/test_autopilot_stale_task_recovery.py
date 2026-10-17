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
    assert _recovery({"task_propose_timeout_seconds": 5})._proposing_stale_seconds(
        SimpleNamespace(task_kind="x", goal_id="")) == rec.PROPOSING_STALE_SECONDS
    assert _recovery({"task_propose_timeout_seconds": 100000})._proposing_stale_seconds(
        SimpleNamespace(task_kind="x", goal_id="")) == rec.PROPOSING_STALE_MAX_SECONDS


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
