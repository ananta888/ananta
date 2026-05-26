from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PixelPoint:
    x: float
    y: float


def smooth_follow(*, current: PixelPoint, target: PixelPoint, speed: float, dt: float) -> PixelPoint:
    safe_speed = max(0.0, float(speed))
    safe_dt = max(0.0, float(dt))
    if safe_speed <= 0.0 or safe_dt <= 0.0:
        return current
    dx = target.x - current.x
    dy = target.y - current.y
    step = min(1.0, safe_speed * safe_dt)
    return PixelPoint(x=current.x + dx * step, y=current.y + dy * step)


def pixel_boost_speed(*, base_speed: float, artifact_intent: str) -> float:
    level = str(artifact_intent or "").strip().lower()
    if level == "confirmed":
        return max(base_speed, base_speed * 2.2)
    if level == "likely":
        return max(base_speed, base_speed * 1.6)
    return max(0.1, base_speed)
