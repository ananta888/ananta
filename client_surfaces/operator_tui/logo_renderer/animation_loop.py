from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrameTiming:
    render_ms: float
    encode_ms: float
    output_ms: float
    total_ms: float


class AnimationLoop:
    def __init__(self, *, target_fps: int = 10, min_fps: int = 5, max_fps: int = 30) -> None:
        self._target_fps = max(1, int(target_fps))
        self._min_fps = max(1, int(min_fps))
        self._max_fps = max(self._target_fps, int(max_fps))
        self._current_fps = min(self._max_fps, max(self._min_fps, self._target_fps))
        self._last_frame_ts = 0.0
        self._skipped_frames = 0

    @property
    def fps(self) -> int:
        return int(self._current_fps)

    @property
    def skipped_frames(self) -> int:
        return int(self._skipped_frames)

    def target_interval(self) -> float:
        return 1.0 / float(max(1, self._current_fps))

    def wait_for_next_frame(self, now: float | None = None) -> float:
        ts = float(now if now is not None else time.monotonic())
        if self._last_frame_ts <= 0.0:
            self._last_frame_ts = ts
            return 0.0
        delta = ts - self._last_frame_ts
        wait_s = max(0.0, self.target_interval() - delta)
        if wait_s > 0:
            time.sleep(wait_s)
            self._last_frame_ts = ts + wait_s
            return wait_s
        self._last_frame_ts = ts
        return 0.0

    def record_timing(self, *, render_ms: float, encode_ms: float, output_ms: float) -> FrameTiming:
        total = max(0.0, float(render_ms) + float(encode_ms) + float(output_ms))
        budget = 1000.0 / float(max(1, self._current_fps))
        if total > budget * 1.2 and self._current_fps > self._min_fps:
            self._current_fps = max(self._min_fps, self._current_fps - 1)
            self._skipped_frames += 1
        elif total < budget * 0.6 and self._current_fps < self._target_fps:
            self._current_fps = min(self._target_fps, self._current_fps + 1)
        return FrameTiming(render_ms=float(render_ms), encode_ms=float(encode_ms), output_ms=float(output_ms), total_ms=total)
