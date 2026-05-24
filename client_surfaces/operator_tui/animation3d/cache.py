from __future__ import annotations

from dataclasses import dataclass, field

from client_surfaces.operator_tui.animation3d.models import FrameResult


@dataclass(frozen=True)
class FrameCacheKey:
    width: int
    height: int
    preset: str
    color_mode: str
    frame_index: int


@dataclass
class FrameCache:
    max_entries: int = 300
    _store: dict[FrameCacheKey, FrameResult] = field(default_factory=dict)

    def get(self, key: FrameCacheKey) -> FrameResult | None:
        return self._store.get(key)

    def put(self, key: FrameCacheKey, result: FrameResult) -> None:
        if len(self._store) >= self.max_entries:
            return
        self._store[key] = result

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


def compute_frame_index(t: float, fps: int, total_frames: int | None = None) -> int:
    idx = int(round(t * fps))
    if total_frames is not None:
        idx = max(0, min(total_frames - 1, idx))
    return idx
