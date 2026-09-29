"""Deterministic futures/clocks: no network, GPU, sleep or interactive approval."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_control_exchange import DialogControlExchange


def setup():
    state = SimpleNamespace(now=100.0)
    pool = Mock(side_effect=None)
    pool.submit.side_effect = lambda *args, **kwargs: Future()
    exchange = DialogControlExchange(Mock(), "synthetic-session", clock=lambda: state.now, pool=pool)
    return state, pool, exchange


def ready(exchange, value):
    exchange.pending.set_result(value)
    assert exchange.poll() is value


def test_delayed_control_reads_never_block_media_tick_or_enqueue_a_backlog():
    clock, pool, exchange = setup()
    assert exchange.poll() is None
    for tick in range(15):
        clock.now = 100 + tick * 0.02
        assert exchange.poll() is None
    assert pool.submit.call_count == 1
    clock.now = 100.3
    ready(exchange, {"synthetic": "current"})
    clock.now = 100.99
    assert exchange.poll() is None and pool.submit.call_count == 1
    clock.now = 101.0
    assert exchange.poll() is None and pool.submit.call_count == 2
    exchange.close()


def test_control_refresh_retains_margin_for_delayed_browser_polling_without_extending_authority():
    clock, pool, exchange = setup()
    exchange.poll()
    ready(exchange, {"version": 1})
    clock.now = 101.4  # 400 ms browser work delayed the due read.
    exchange.poll()
    assert pool.submit.call_count == 2
    clock.now = 101.7  # A bounded 300 ms transport round trip.
    exchange.pending.set_result({"version": 2})
    clock.now = 102.1  # Another delayed media tick, still before the old expiry.
    assert exchange.poll() == {"version": 2}
    assert exchange.fresh_until == 104.6  # Still exactly 2.5 seconds, never widened.
    exchange.close()


def test_generation_completion_requires_a_new_request_not_an_old_inflight_response():
    clock, pool, exchange = setup()
    exchange.poll()
    ready(exchange, {"version": 1})
    clock.now += 2
    exchange.poll()
    previous = exchange.pending
    assert exchange.poll(refresh_marker=1) is None
    previous.set_result({"version": "before-generation"})
    assert exchange.poll(refresh_marker=1) is None
    assert pool.submit.call_count == 3 and exchange.pending is not previous
    exchange.pending.set_result({"version": 2})
    assert exchange.poll(refresh_marker=1) == {"version": 2}
    exchange.close()


@pytest.mark.parametrize("established", [False, True])
def test_stale_control_read_cannot_extend_or_revive_current_authority(established):
    clock, _, exchange = setup()
    exchange.poll()
    if established:
        ready(exchange, {"version": 1})
        clock.now += 2
        exchange.poll()
        clock.now += 0.5
    else:
        clock.now += 2.5
    exchange.pending.set_result({"stale": True})
    with pytest.raises(ValueError, match="stale"):
        exchange.poll()
    exchange.close()


def test_obsolete_denial_is_not_retried_and_pending_work_cannot_reopen_after_close():
    _, pool, exchange = setup()
    exchange.poll()
    pending = exchange.pending
    exchange.refresh()
    pending.set_exception(ValueError("synthetic-denied"))
    with pytest.raises(ValueError, match="denied"):
        exchange.poll()
    assert pool.submit.call_count == 1
    exchange.close()
    with pytest.raises(ValueError, match="closed"):
        exchange.poll()
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_renewal_refresh_starts_one_new_read_without_waiting_for_normal_cadence():
    _, pool, exchange = setup()
    exchange.poll()
    ready(exchange, {"renewal": True})
    exchange.refresh()
    exchange.poll()
    assert pool.submit.call_count == 2
    pending = exchange.pending
    exchange.close()
    assert pending.cancelled()


def test_running_http_completion_after_close_cannot_apply_or_schedule_again():
    _, pool, exchange = setup()
    exchange.poll()
    pending = exchange.pending
    assert pending.set_running_or_notify_cancel()
    exchange.close()
    assert not pending.cancelled()  # The transport deadline owns this request.
    pending.set_result({"late": "verified-but-no-longer-owned"})
    with pytest.raises(ValueError, match="closed"):
        exchange.poll()
    with pytest.raises(ValueError, match="closed"):
        exchange.refresh()
    assert pool.submit.call_count == 1 and exchange.pending is None
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_renewal_checkpoint_requires_established_fresh_state_without_any_io_or_extension():
    clock, pool, exchange = setup()
    with pytest.raises(ValueError, match="state_stale"):
        exchange.require_fresh()
    pool.submit.assert_not_called()
    exchange.poll()
    ready(exchange, {"renewal": True})
    for offset in [0, 1, 2.49]:
        clock.now = 100 + offset
        exchange.require_fresh()
        assert exchange.fresh_until == 102.5 and pool.submit.call_count == 1
    clock.now = 102.5
    with pytest.raises(ValueError, match="state_stale"):
        exchange.require_fresh()
    exchange.close()
    with pytest.raises(ValueError, match="closed"):
        exchange.require_fresh()
