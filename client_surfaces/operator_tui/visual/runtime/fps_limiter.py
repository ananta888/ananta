from __future__ import annotations


class FpsLimiter:
    def __init__(self, *, fps: int) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self._interval = 1.0 / float(fps)
        self._next_allowed_at = 0.0

    def allow(self, now: float) -> bool:
        if now < self._next_allowed_at:
            return False
        self._next_allowed_at = now + self._interval
        return True

    def reset(self, *, now: float) -> None:
        self._next_allowed_at = float(now)

