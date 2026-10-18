"""Non-blocking dispatch state: submit without waiting, reap finished results."""
import time

from agent.routes.tasks.autopilot_async_dispatch import AsyncDispatchState, async_dispatch_enabled


def test_async_dispatch_enabled_reads_autopilot_config():
    assert async_dispatch_enabled({"autopilot": {"async_dispatch_enabled": True}}) is True
    assert async_dispatch_enabled({"autopilot": {"async_dispatch_enabled": False}}) is False
    assert async_dispatch_enabled({}) is False
    assert async_dispatch_enabled(None) is False


def test_submit_tracks_inflight_and_reap_collects():
    state = AsyncDispatchState(max_workers=2)
    try:
        state.submit(lambda: 'done', task_id='t1')
        assert state.inflight() == 1
        results = _reap_until(state, 1)
        assert results == [('t1', 'done')]
        assert state.inflight() == 0
    finally:
        state.shutdown()


def test_reap_reports_exceptions():
    state = AsyncDispatchState(max_workers=1)
    try:
        def boom():
            raise RuntimeError('nope')

        state.submit(boom, task_id='t2')
        results = _reap_until(state, 1)
        assert results[0][0] == 't2'
        assert isinstance(results[0][1], RuntimeError)
    finally:
        state.shutdown()


def _reap_until(state, count, timeout=2.0):
    deadline = time.time() + timeout
    collected = []
    while time.time() < deadline:
        collected.extend(state.reap())
        if len(collected) >= count:
            return collected
        time.sleep(0.01)
    return collected
