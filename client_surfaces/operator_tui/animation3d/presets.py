from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class AnimationPreset:
    name: str
    duration_ms: int = 2000
    fps: int = 24
    rotation_speed: float = 0.5
    snake_phase_offset: float = 1.0
    scale_curve: Callable[[float], float] = field(default=lambda t: 1.0)
    color_mode: str = "truecolor"
    a_color: str = "0,180,80"
    snake_color: str = "200,120,40"

    def scale_at(self, t: float) -> float:
        return self.scale_curve(t)


def _linear_scale(t: float) -> float:
    return 0.7 + t * 0.3


def _pulse_scale(t: float) -> float:
    return 0.8 + 0.2 * math.sin(t * math.pi * 2.0)


def _snake_orbit_scale(t: float) -> float:
    return 0.8 + 0.1 * math.sin(t * math.pi * 0.5)


builtin_presets: dict[str, AnimationPreset] = {
    "rotate_in": AnimationPreset(
        name="rotate_in",
        duration_ms=2000,
        fps=24,
        rotation_speed=0.5,
        snake_phase_offset=1.0,
        scale_curve=_linear_scale,
        a_color="0,180,80",
        snake_color="200,120,40",
    ),
    "snake_orbit": AnimationPreset(
        name="snake_orbit",
        duration_ms=2500,
        fps=20,
        rotation_speed=0.3,
        snake_phase_offset=2.0,
        scale_curve=_snake_orbit_scale,
        a_color="0,160,100",
        snake_color="220,100,30",
    ),
    "depth_pulse": AnimationPreset(
        name="depth_pulse",
        duration_ms=3000,
        fps=15,
        rotation_speed=0.2,
        snake_phase_offset=1.5,
        scale_curve=_pulse_scale,
        a_color="30,180,120",
        snake_color="200,80,60",
    ),
}
