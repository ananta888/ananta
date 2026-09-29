from __future__ import annotations

import pytest

from agent.cli.splash import SplashMachine, SplashState, SplashTransitionError, SplashContext
from agent.cli.status_snapshot import StatusSnapshot


def _clock(offsets: list[float]) -> float:
    if not offsets:
        return 0.0
    return offsets.pop(0)


def test_initial_state_fullscreen():
    sm = SplashMachine(fullscreen_seconds=2.0, clock=lambda: 0.0)
    assert sm.context.state == SplashState.FULLSCREEN


def test_initial_state_fullscreen_time():
    sm = SplashMachine(fullscreen_seconds=2.0, clock=lambda: 0.0)
    assert sm.context.state == SplashState.FULLSCREEN
    assert sm.context.entered_at == 0.0


def test_transition_to_compact_header():
    sm = SplashMachine(fullscreen_seconds=0.0, transition_seconds=0.001, clock=lambda: 0.0)
    sm.tick(now=0.0)
    assert sm.context.state == SplashState.TRANSITION
    sm.tick(now=1.0)
    assert sm.context.state == SplashState.COMPACT_HEADER


def test_tick_advances_to_transition():
    sm = SplashMachine(fullscreen_seconds=1.0, transition_seconds=1.0, clock=lambda: 0.0)
    sm.tick(now=0.0)
    assert sm.context.state == SplashState.FULLSCREEN
    sm.tick(now=1.5)
    assert sm.context.state == SplashState.TRANSITION


def test_tick_fullscreen_to_compact_header():
    sm = SplashMachine(fullscreen_seconds=1.0, transition_seconds=0.5, clock=lambda: 0.0)
    sm.tick(now=0.0)
    assert sm.context.state == SplashState.FULLSCREEN
    sm.tick(now=1.5)
    assert sm.context.state == SplashState.TRANSITION
    sm.tick(now=2.5)
    assert sm.context.state == SplashState.COMPACT_HEADER


def test_transition_progress():
    sm = SplashMachine(fullscreen_seconds=0.0, transition_seconds=2.0, clock=lambda: 0.0)
    sm.tick(now=0.0)
    assert sm.context.state == SplashState.TRANSITION
    assert sm.context.transition_progress == 0.0
    sm.tick(now=1.0)
    assert sm.context.transition_progress == 0.5
    sm.tick(now=2.0)
    assert sm.context.state == SplashState.COMPACT_HEADER


def test_skip():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.skip()
    assert sm.context.state == SplashState.SKIPPED


def test_disable():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.disable()
    assert sm.context.state == SplashState.DISABLED


def test_reset():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.transition_to(SplashState.COMPACT_HEADER)
    sm.reset()
    assert sm.context.state == SplashState.FULLSCREEN


def test_update_status():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    snap = StatusSnapshot(tasks_queued=5)
    sm.update_status(snap)
    assert sm.context.status.tasks_queued == 5


def test_invalid_transition_from_disabled():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.disable()
    with pytest.raises(SplashTransitionError):
        sm.transition_to(SplashState.FULLSCREEN)


def test_invalid_transition_raises():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.disable()
    with pytest.raises(SplashTransitionError):
        sm.transition_to(SplashState.FULLSCREEN)


def test_render_disabled_returns_empty():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.disable()
    result = sm.render(StatusSnapshot())
    assert result == []


def test_render_skipped_returns_empty():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    sm.skip()
    result = sm.render(StatusSnapshot())
    assert result == []


def test_render_fullscreen_returns_logo_lines():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    result = sm.render(StatusSnapshot(), width=90, color=False)
    assert len(result) > 0


def test_splash_context_elapsed():
    ctx = SplashContext(entered_at=100.0)
    assert ctx.elapsed(105.0) == 5.0
    assert ctx.elapsed(100.0) == 0.0


def test_splash_context_elapsed_unset():
    ctx = SplashContext(entered_at=0.0)
    assert ctx.elapsed(10.0) == 10.0


def test_transition_to_same_state_is_noop():
    sm = SplashMachine(fullscreen_seconds=5.0, clock=lambda: 0.0)
    ctx = sm.context
    sm.transition_to(SplashState.FULLSCREEN)
    assert sm.context == ctx
