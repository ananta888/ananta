from __future__ import annotations

from client_surfaces.operator_tui.visual.runtime.frame_scheduler import FrameScheduler


def test_frame_scheduler_limits_render_rate_to_target_fps() -> None:
    scheduler = FrameScheduler(target_fps=10, max_fps=30)
    rendered = 0
    now = 0.0
    for _ in range(20):
        if scheduler.should_render(now=now):
            rendered += 1
        now += 0.05
    # Simulated 1 second in 0.05 steps => max 10 frames at 10 FPS.
    assert rendered <= 10


def test_frame_scheduler_dirty_only_mode_requires_dirty_flag() -> None:
    scheduler = FrameScheduler(target_fps=10, max_fps=30, dirty_only=True)
    scheduler.clear_dirty()
    assert scheduler.should_render(now=0.0) is False
    scheduler.mark_dirty()
    assert scheduler.should_render(now=0.2) is True


def test_frame_scheduler_pause_resume() -> None:
    scheduler = FrameScheduler(target_fps=10, max_fps=30)
    scheduler.pause()
    assert scheduler.should_render(now=0.0) is False
    scheduler.resume(now=0.0)
    assert scheduler.should_render(now=0.1) is True

