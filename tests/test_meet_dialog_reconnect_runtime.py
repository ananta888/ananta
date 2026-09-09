"""Real session/pulse logic under virtual time and explicitly synthetic Hub policy."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_session_operations import setup as session_setup
from tests.test_meet_dialog_session_operations import starts
from worker.meet_media.dialog_control_exchange import DialogControlExchange
from worker.meet_media.dialog_reconnect import reconnect_session


def setup(*, deadline=200, grant_at=104):
    page, session = session_setup(deadline)
    assignment = {"reconnect": True, "capabilities": [], "avatar_videos": False}
    progress = Mock()
    granted = []
    calls = []

    def call(action, **fields):
        calls.append((page.now, action, fields))
        meeting = None
        if page.now >= grant_at and not granted:
            granted.append(page.now)
            meeting = {"room_id": "synthetic-room", "grant": "synthetic-fresh"}
        return {
            "attempt": 1,
            "state": "joining" if granted else "waiting",
            "deadline_ms": 130000,
            "ready_ms": 104000,
            "meeting": meeting,
        }  # Signature/window behavior is separately covered by real HTTP tests.

    hub = SimpleNamespace(deadline=deadline, call=Mock(side_effect=call))
    pool = Mock()

    def submit(fn):
        future = Future()
        try:
            future.set_result(fn())
        except Exception as error:
            future.set_exception(error)
        return future

    pool.submit.side_effect = submit
    exchanges = []

    def factory(*args, **kwargs):
        exchange = DialogControlExchange(*args, pool=pool, **kwargs)
        exchanges.append(exchange)
        return exchange

    def run(**kwargs):
        return reconnect_session(
            assignment,
            hub,
            page,
            session,
            "synthetic-old",
            progress=progress,
            clock=lambda: page.now,
            wall_clock=lambda: page.now,
            exchange_factory=factory,
            **kwargs,
        )

    return SimpleNamespace(**locals())


def test_quiet_wait_requires_fresh_hub_pulses_then_hands_off_one_grant_with_original_deadline():
    f = setup()
    f.run()
    assert f.page.now >= 104 and len(f.granted) == 1
    assert starts(f.page) == [["join", ["synthetic-room", "synthetic-fresh"], f.page.url]]
    assert f.calls[0][2]["attempt"] == 0
    assert all(
        row[1] == "reconnect" and row[2] == {"meet_session_id": "synthetic-old", "attempt": 1} for row in f.calls[1:]
    )
    assert len(f.calls) >= 5 and f.hub.deadline == f.session.deadline == 200
    assert all(call.args[1] == 200 and call.args[0] <= 130 for call in f.progress.report.call_args_list)
    assert f.exchanges[0].closed
    f.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


@pytest.mark.parametrize("deadline,expected", [(102, 102), (200, 130)])
def test_continuous_successful_waiting_replies_never_extend_task_or_attempt(deadline, expected):
    f = setup(deadline=deadline, grant_at=300)
    with pytest.raises(ValueError, match="stale"):
        f.run()
    assert f.page.now == pytest.approx(expected) and not starts(f.page)
    assert f.exchanges[0].closed and not f.granted


def test_pending_hub_request_never_refreshes_watchdog_from_a_timer():
    f = setup()
    f.pool.submit.side_effect = lambda fn: Future()
    with pytest.raises(ValueError, match="stale"):
        f.run()
    assert f.page.now == pytest.approx(102.5) and not starts(f.page)
    f.progress.report.assert_called_once_with(102.5, 200)
    assert f.pool.submit.call_count == 1 and f.exchanges[0].closed


@pytest.mark.parametrize("phase", ["admission", "waiting", "join"])
def test_policy_denial_is_terminal_without_grant_replay_or_second_admission(phase):
    f = setup()
    call = f.hub.call.side_effect

    def denied(action, **fields):
        if phase == "admission" or (phase == "waiting" and fields["attempt"] == 1) or (phase == "join" and f.granted):
            raise ValueError("synthetic_policy_denied")
        return call(action, **fields)

    f.hub.call.side_effect = denied
    if phase == "join":
        f.page.state = "pending"
    with pytest.raises(ValueError, match="policy_denied"):
        f.run()
    assert sum(call.kwargs["attempt"] == 0 for call in f.hub.call.call_args_list) == 1
    assert len(starts(f.page)) == int(phase == "join")
    assert all(exchange.closed for exchange in f.exchanges)


def test_never_resolving_join_keeps_pulses_but_cannot_extend_its_20_second_bound():
    f = setup()
    f.page.state = "pending"
    with pytest.raises(ValueError, match="session_expired"):
        f.run()
    assert 124 <= f.page.now < 125 and len(starts(f.page)) == 1
    assert len(f.calls) >= 20 and f.session.closed and f.exchanges[0].closed


def test_readiness_also_requires_fresh_hub_policy_and_never_hands_off_after_revocation():
    f = setup()
    f.page.ready = False
    call = f.hub.call.side_effect
    f.hub.call.side_effect = (
        lambda action, **fields: call(action, **fields)
        if f.page.now < 106
        else (_ for _ in ()).throw(ValueError("synthetic_revoked"))
    )
    with pytest.raises(ValueError, match="revoked"):
        f.run()
    assert not starts(f.page) and f.session.closed and f.exchanges[0].closed
