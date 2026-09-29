from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow


def test_smooth_follow_moves_toward_target() -> None:
    current = PixelPoint(0.0, 0.0)
    target = PixelPoint(100.0, 50.0)
    updated = smooth_follow(current=current, target=target, speed=2.0, dt=0.2)
    assert updated.x > current.x
    assert updated.y > current.y
    assert updated.x < target.x
    assert updated.y < target.y


def test_pixel_boost_speed_uses_intent_levels() -> None:
    base = 2.0
    assert pixel_boost_speed(base_speed=base, artifact_intent="none") == base
    assert pixel_boost_speed(base_speed=base, artifact_intent="likely") > base
    assert pixel_boost_speed(base_speed=base, artifact_intent="confirmed") > pixel_boost_speed(base_speed=base, artifact_intent="likely")
