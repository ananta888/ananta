from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.animation_loop import AnimationLoop


def test_animation_loop_reduces_fps_when_budget_exceeded() -> None:
    loop = AnimationLoop(target_fps=10, min_fps=5, max_fps=20)
    baseline = loop.fps
    _ = loop.record_timing(render_ms=80, encode_ms=30, output_ms=30)
    assert loop.fps < baseline
    assert loop.skipped_frames >= 1


def test_animation_loop_recovers_towards_target() -> None:
    loop = AnimationLoop(target_fps=10, min_fps=5, max_fps=20)
    _ = loop.record_timing(render_ms=80, encode_ms=30, output_ms=30)
    lowered = loop.fps
    for _ in range(4):
        _ = loop.record_timing(render_ms=10, encode_ms=8, output_ms=8)
    assert loop.fps >= lowered
    assert loop.fps <= 10
