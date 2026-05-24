from __future__ import annotations

from client_surfaces.operator_tui.animation3d.cache import FrameCache, FrameCacheKey, compute_frame_index
from client_surfaces.operator_tui.animation3d.models import FrameResult


class TestFrameCache:
    def test_cache_miss(self):
        cache = FrameCache()
        key = FrameCacheKey(120, 32, "rotate_in", "truecolor", 0)
        assert cache.get(key) is None

    def test_cache_hit(self):
        cache = FrameCache()
        key = FrameCacheKey(120, 32, "rotate_in", "truecolor", 0)
        result = FrameResult(text="hello", visible_width=120, visible_height=32, ansi_used=True)
        cache.put(key, result)
        cached = cache.get(key)
        assert cached is not None
        assert cached.text == "hello"

    def test_max_entries(self):
        cache = FrameCache(max_entries=10)
        for i in range(20):
            key = FrameCacheKey(120, 32, "rotate_in", "truecolor", i)
            result = FrameResult(text=str(i), visible_width=120, visible_height=32, ansi_used=True)
            cache.put(key, result)
        assert len(cache) <= 10

    def test_clear(self):
        cache = FrameCache()
        key = FrameCacheKey(120, 32, "rotate_in", "truecolor", 0)
        cache.put(key, FrameResult(text="x", visible_width=120, visible_height=32, ansi_used=True))
        cache.clear()
        assert len(cache) == 0


class TestComputeFrameIndex:
    def test_index_at_zero(self):
        assert compute_frame_index(0.0, 24) == 0

    def test_index_at_half_second(self):
        assert compute_frame_index(0.5, 24) == 12

    def test_index_at_one_second(self):
        assert compute_frame_index(1.0, 24) == 24

    def test_index_bounded(self):
        assert compute_frame_index(10.0, 24, total_frames=60) == 59
