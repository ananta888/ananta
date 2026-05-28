from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame


@dataclass(frozen=True)
class FrameCacheKey:
    view_id: str
    renderer_id: str
    width: int
    height: int
    state_version: str
    theme_version: str = ""


@dataclass(frozen=True)
class FrameCacheStats:
    hits: int
    misses: int
    evictions: int


class FrameCache:
    def __init__(self, *, max_entries: int = 64) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._store: OrderedDict[FrameCacheKey, RenderFrame] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: FrameCacheKey) -> RenderFrame | None:
        hit = self._store.get(key)
        if hit is None:
            self._misses += 1
            return None
        self._hits += 1
        self._store.move_to_end(key)
        return hit

    def put(self, key: FrameCacheKey, frame: RenderFrame) -> None:
        self._store[key] = frame
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> FrameCacheStats:
        return FrameCacheStats(hits=self._hits, misses=self._misses, evictions=self._evictions)


@dataclass(frozen=True)
class BackpressureStats:
    dropped_frames: int
    queued_frames: int


class FrameBackpressureBuffer:
    def __init__(self) -> None:
        self._pending: tuple[RenderFrame, bool] | None = None
        self._dropped_frames = 0

    def offer(self, frame: RenderFrame, *, is_animation: bool) -> bool:
        if self._pending is None:
            self._pending = (frame, is_animation)
            return True
        _, pending_is_animation = self._pending
        if pending_is_animation and is_animation:
            self._pending = (frame, True)
            self._dropped_frames += 1
            return True
        if (not pending_is_animation) and is_animation:
            self._dropped_frames += 1
            return False
        self._pending = (frame, is_animation)
        self._dropped_frames += 1
        return True

    def pop(self) -> RenderFrame | None:
        if self._pending is None:
            return None
        frame, _ = self._pending
        self._pending = None
        return frame

    def has_pending(self) -> bool:
        return self._pending is not None

    def stats(self) -> BackpressureStats:
        return BackpressureStats(
            dropped_frames=self._dropped_frames,
            queued_frames=1 if self._pending is not None else 0,
        )

