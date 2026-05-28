from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.fps_limiter import FpsLimiter


@dataclass(frozen=True)
class SchedulerStats:
    rendered_frames: int
    skipped_frames: int
    dropped_frames: int


class FrameScheduler:
    def __init__(self, *, target_fps: int, max_fps: int, dirty_only: bool = False) -> None:
        if target_fps <= 0 or max_fps <= 0:
            raise ValueError("fps must be positive")
        if target_fps > max_fps:
            raise ValueError("target_fps must be <= max_fps")
        self._dirty_only = bool(dirty_only)
        self._dirty = True
        self._paused = False
        self._target_limiter = FpsLimiter(fps=target_fps)
        self._max_limiter = FpsLimiter(fps=max_fps)
        self._rendered_frames = 0
        self._skipped_frames = 0
        self._dropped_frames = 0

    def mark_dirty(self) -> None:
        self._dirty = True

    def clear_dirty(self) -> None:
        self._dirty = False

    def set_dirty_only(self, enabled: bool) -> None:
        self._dirty_only = bool(enabled)

    def pause(self) -> None:
        self._paused = True

    def resume(self, *, now: float) -> None:
        self._paused = False
        self._target_limiter.reset(now=now)
        self._max_limiter.reset(now=now)

    def should_render(self, *, now: float, force: bool = False) -> bool:
        if self._paused and not force:
            self._skipped_frames += 1
            return False
        if self._dirty_only and not self._dirty and not force:
            self._skipped_frames += 1
            return False
        if not self._max_limiter.allow(now):
            self._dropped_frames += 1
            return False
        if not force and not self._target_limiter.allow(now):
            self._skipped_frames += 1
            return False
        self._rendered_frames += 1
        self._dirty = False
        return True

    def stats(self) -> SchedulerStats:
        return SchedulerStats(
            rendered_frames=self._rendered_frames,
            skipped_frames=self._skipped_frames,
            dropped_frames=self._dropped_frames,
        )

