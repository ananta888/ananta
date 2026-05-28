from __future__ import annotations

from client_surfaces.operator_tui.visual.runtime.frame_cache import FrameBackpressureBuffer, FrameCache, FrameCacheKey
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame


def _frame(label: str, *, animated: bool = False) -> RenderFrame:
    return RenderFrame(
        frame_type="ansi",
        width=80,
        height=24,
        payload=[label],
        mime_or_format="text/plain",
        timestamp=1.0,
        metadata={"animated": animated},
    )


def test_frame_cache_hit_miss_and_eviction() -> None:
    cache = FrameCache(max_entries=1)
    key1 = FrameCacheKey("v1", "r1", 80, 24, "s1")
    key2 = FrameCacheKey("v1", "r1", 80, 24, "s2")
    assert cache.get(key1) is None
    cache.put(key1, _frame("a"))
    assert cache.get(key1) is not None
    cache.put(key2, _frame("b"))
    assert cache.get(key1) is None
    stats = cache.stats()
    assert stats.hits >= 1
    assert stats.misses >= 2
    assert stats.evictions == 1


def test_backpressure_drops_stale_animation_frames() -> None:
    pressure = FrameBackpressureBuffer()
    assert pressure.offer(_frame("f1", animated=True), is_animation=True) is True
    assert pressure.offer(_frame("f2", animated=True), is_animation=True) is True
    pending = pressure.pop()
    assert pending is not None
    assert pending.payload == ["f2"]
    assert pressure.stats().dropped_frames == 1

