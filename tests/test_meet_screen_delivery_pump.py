"""Screen scheduling is independent of an unfinished browser JPEG decode."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_transport import assignment
from worker.meet_media.dialog_screen_pump import DialogScreenPump


def setup():
    state = SimpleNamespace(now=100.0, opened=False, generation=0, starts=[])
    page = Mock()
    frames = Mock(busy=False)
    source = Mock(source_id="screen:synthetic")
    source.take.return_value = "latest-jpeg"
    factory = Mock(return_value=source)

    def begin(*args):
        state.starts.append(state.now)
        frames.busy = True

    frames.begin.side_effect = begin
    frames.poll.return_value = "pending"

    def cancel():
        frames.busy = False

    frames.cancel.side_effect = cancel

    def evaluate(script, *args):
        if "screen.status" in script:
            return state.opened
        if "screen.open" in script:
            state.opened = True
            state.generation += 1
            return {"generation": state.generation}
        raise AssertionError("transport must use its injected port")

    page.evaluate.side_effect = evaluate
    pump = DialogScreenPump(
        page,
        Mock(),
        assignment() | {"capabilities": ["screen.publish"]},
        factory,
        frames=frames,
        clock=lambda: state.now,
    )
    control = {"enabled": True, "revision": 1}
    pump.update(control)
    frames.reset_mock()
    return SimpleNamespace(**locals())


def test_pending_decode_never_reads_another_frame_and_success_keeps_5fps_ceiling():
    f = setup()
    f.pump.tick()
    f.frames.begin.assert_called_once_with(1, 1, "latest-jpeg")
    for offset in [0.02, 0.1, 0.3, 0.5]:
        f.state.now = 100 + offset
        f.pump.tick()
    f.source.take.assert_called_once()
    f.frames.begin.assert_called_once()
    assert f.pump.sequence == 1 and f.frames.poll.call_count == 4

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    f.source.take.return_value = "new-latest-jpeg"
    f.pump.tick()
    f.state.now = 100.501
    f.pump.tick()
    f.frames.begin.assert_called_with(1, 2, "new-latest-jpeg")
    assert f.frames.begin.call_count == 2
    f.state.now = 100.502
    f.pump.tick()  # Completion before the next deadline cannot send early.
    f.state.now = 100.699
    f.pump.tick()
    assert f.frames.begin.call_count == 2
    f.state.now = 100.702
    f.pump.tick()
    assert f.frames.begin.call_count == 3
    assert all(after - before >= 0.2 for before, after in zip(f.state.starts, f.state.starts[1:]))
    f.page.wait_for_timeout.assert_not_called()


def test_pending_decode_cannot_trigger_source_reopen_during_fresh_hub_update():
    f = setup()
    f.pump.tick()
    f.state.opened = False
    f.pump.update(f.control)
    assert f.state.generation == 1

    def stale():
        f.frames.busy = False
        return "stale"

    f.frames.poll.side_effect = stale
    f.pump.tick()
    assert not f.pump.failed and f.pump.lease is None
    f.state.now += 1
    f.pump.tick()
    assert f.state.generation == 1
    f.pump.update(f.control)
    assert f.state.generation == 2 and f.pump.sequence == 0


@pytest.mark.parametrize("leased", [True, False])
def test_tick_uses_delivery_authority_without_a_redundant_status_roundtrip(leased):
    f = setup()
    f.page.reset_mock()
    if not leased:
        f.pump.lease = None
    f.pump.tick()
    f.page.evaluate.assert_not_called()
    assert f.source.take.call_count == int(leased)
    assert f.frames.begin.call_count == int(leased)
    assert f.state.generation == 1


def test_source_validation_failure_still_closes_without_frame_submission():
    f = setup()
    f.source.take.side_effect = ValueError("meet_screen_content_denied")
    f.page.reset_mock()
    f.pump.tick()
    assert f.pump.failed and f.pump.lease is None and f.pump.source is None
    f.frames.begin.assert_not_called()
    f.frames.close_generation.assert_called_once_with(1)
    f.source.close.assert_called_once()
    f.pump.update(f.control)
    f.factory.assert_called_once()  # Same Hub revision cannot revive a failed source.


def test_recorded_soak_tick_can_submit_before_the_later_scheduling_gap():
    f = setup()
    f.pump.tick()

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    f.state.now = 100.13213
    f.pump.tick()
    f.state.now = 100.27417
    f.pump.tick()
    assert f.frames.begin.call_count == 2, "completion must not postpone the original 200-ms frame opportunity"
    assert f.pump.sequence == 2 and f.frames.busy


def test_late_completion_can_submit_only_one_due_latest_frame_in_the_same_tick():
    f = setup()
    f.pump.tick()

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    f.source.take.return_value = "latest-after-gap"
    f.state.now = 100.45622
    f.pump.tick()
    assert f.state.starts == [100.0, 100.45622]
    f.frames.begin.assert_called_with(1, 2, "latest-after-gap")
    assert f.frames.busy and f.frames.poll.call_count == 1
    f.pump.tick()
    assert f.frames.begin.call_count == 2, "no catch-up burst at the same clock instant"


def test_recorded_late_completion_trace_keeps_existing_pre_tick_freshness_limit():
    f = setup()
    f.pump.tick()

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    # Relative to sequence70 in the failed 88-minute run. A synthetic 25-ms
    # completion delay models the async slot; it is not a measured latency.
    # The skipped opportunity exceeds the unchanged 750-ms freshness limit.
    for offset in (0.45622, 0.59977, 1.07708, 1.54160):
        f.state.now = 100 + offset
        age = f.state.now - (f.state.starts[-1] + 0.025)
        assert age <= 0.75, "late completion postponed an already-due frame beyond the unchanged freshness bound"
        f.pump.tick()
    assert len(f.state.starts) == 4
    assert all(after - before >= 0.2 for before, after in zip(f.state.starts, f.state.starts[1:]))


@pytest.mark.parametrize("completion", [0.01, 0.13213, 0.5])
def test_completed_decode_does_not_add_another_idle_interval(completion):
    f = setup()
    f.pump.tick()

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    f.state.now = 100 + completion
    f.pump.tick()
    f.state.now = 100 + max(0.201, completion + 0.001)
    f.pump.tick()
    assert f.frames.begin.call_count == 2
    assert f.pump.next_frame == pytest.approx(f.state.starts[-1] + 0.2)


def test_delayed_begin_ack_does_not_restart_the_host_start_interval():
    f = setup()
    begin = f.frames.begin.side_effect

    def delayed(*args):
        begin(*args)
        f.state.now += 0.3638

    f.frames.begin.side_effect = delayed
    f.pump.tick()
    assert f.pump.next_frame == pytest.approx(100.2)

    def done():
        f.frames.busy = False
        return "done"

    f.frames.poll.side_effect = done
    f.frames.begin.side_effect = begin
    f.state.now = 100.52684
    f.pump.tick()
    assert f.state.starts == [100.0, 100.52684]


def test_delayed_ack_trace_keeps_the_existing_freshness_fence():
    f = setup()
    begin = f.frames.begin.side_effect

    def delayed_once(*args):
        begin(*args)
        f.state.now += 0.36
        f.frames.begin.side_effect = begin

    def done():
        f.frames.busy = False
        return "done"

    f.frames.begin.side_effect = delayed_once
    f.frames.poll.side_effect = done
    f.pump.tick()
    # A synthetic 25-ms browser submission delay is not a measured cross-clock
    # offset. The first acknowledgement arrives after the pacing slot is ready.
    for offset in (0.49, 0.89, 1.15):
        f.state.now = 100 + offset
        assert f.state.now - (f.state.starts[-1] + 0.025) <= 0.75
        f.pump.tick()
    assert len(f.state.starts) == 4
    assert all(after - before >= 0.2 for before, after in zip(f.state.starts, f.state.starts[1:]))


@pytest.mark.parametrize("operation", ["pause", "invalidate", "close", "failure"])
def test_cancellation_drops_pending_frame_and_closes_only_its_generation(operation):
    f = setup()
    f.pump.tick()
    if operation == "pause":
        f.pump.update({"enabled": False, "revision": 2})
    elif operation == "failure":
        f.frames.poll.side_effect = ValueError("synthetic_decode_failed")
        f.pump.tick()
    else:
        getattr(f.pump, operation)()
    assert f.pump.lease is None and not f.frames.busy
    f.frames.close_generation.assert_called_once_with(1)
    f.pump.tick()
    f.frames.begin.assert_called_once()
    if operation == "failure":
        assert f.pump.failed and f.pump.source is None
        f.pump.update(f.control)
        f.factory.assert_called_once()
