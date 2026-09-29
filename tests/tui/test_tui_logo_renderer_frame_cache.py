from __future__ import annotations

import os
from pathlib import Path

from client_surfaces.operator_tui.logo_renderer import frame_cache
from client_surfaces.operator_tui.logo_renderer.frame_cache import LogoFrameCache


def _write_svg(path: Path, fill: str) -> None:
    path.write_text(
        (
            "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'>"
            f"<rect x='0' y='0' width='10' height='10' fill='{fill}'/>"
            "</svg>"
        ),
        encoding="utf-8",
    )


def test_cache_hit_reuses_rendered_frames(monkeypatch, tmp_path):
    svg = tmp_path / "logo.svg"
    _write_svg(svg, "red")

    calls = {"render": 0}

    monkeypatch.setattr(frame_cache, "_rasterize_svg_rgba", lambda **kwargs: object())

    def _fake_render(_img, **kwargs):
        calls["render"] += 1
        return ["frame"]

    monkeypatch.setattr(frame_cache, "render_halfblock_image", _fake_render)

    cache = LogoFrameCache()
    one = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=1)
    two = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=1)

    assert one == [["frame"]]
    assert two == [["frame"]]
    assert calls["render"] == 1


def test_cache_invalidation_on_svg_change(monkeypatch, tmp_path):
    svg = tmp_path / "logo.svg"
    _write_svg(svg, "red")

    calls = {"render": 0}
    monkeypatch.setattr(frame_cache, "_rasterize_svg_rgba", lambda **kwargs: object())

    def _fake_render(_img, **kwargs):
        calls["render"] += 1
        return [f"frame-{calls['render']}"]

    monkeypatch.setattr(frame_cache, "render_halfblock_image", _fake_render)

    cache = LogoFrameCache()
    first = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=1)
    _write_svg(svg, "blue")
    os.utime(svg, None)
    second = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=1)

    assert first == [["frame-1"]]
    assert second == [["frame-2"]]
    assert calls["render"] == 2


def test_cache_miss_for_different_preset(monkeypatch, tmp_path):
    svg = tmp_path / "logo.svg"
    _write_svg(svg, "red")

    calls = {"render": 0}
    monkeypatch.setattr(frame_cache, "_rasterize_svg_rgba", lambda **kwargs: object())
    monkeypatch.setattr(frame_cache, "_apply_preset", lambda image, **kwargs: image)

    def _fake_render(_img, **kwargs):
        calls["render"] += 1
        return [f"frame-{calls['render']}"]

    monkeypatch.setattr(frame_cache, "render_halfblock_image", _fake_render)

    cache = LogoFrameCache()
    _ = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=1)
    _ = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="pulse", frame_count=2)

    assert calls["render"] >= 2


def test_static_preset_returns_single_stable_frame(monkeypatch, tmp_path):
    svg = tmp_path / "logo.svg"
    _write_svg(svg, "red")

    monkeypatch.setattr(frame_cache, "_rasterize_svg_rgba", lambda **kwargs: object())
    monkeypatch.setattr(frame_cache, "render_halfblock_image", lambda _img, **kwargs: ["stable"])

    cache = LogoFrameCache()
    frames = cache.get_ansi_frames(svg_path=str(svg), width_cells=20, height_cells=8, preset="static", frame_count=8)
    assert frames == [["stable"]]


def test_high_quality_oversampling_renders_larger_then_downsamples(monkeypatch):
    class _FakeImage:
        def __init__(self):
            self.resize_calls: list[tuple[int, int]] = []

        def resize(self, size, resample=None):
            self.resize_calls.append((int(size[0]), int(size[1])))
            return self

    calls: list[tuple[int, int]] = []
    fake = _FakeImage()

    def _fake_rasterize(*, svg_path: str, width_px: int, height_px: int):
        calls.append((int(width_px), int(height_px)))
        return fake

    monkeypatch.setenv("ANANTA_TUI_LOGO_QUALITY", "high")
    monkeypatch.delenv("ANANTA_TUI_LOGO_OVERSAMPLING", raising=False)
    monkeypatch.setattr(frame_cache, "rasterize_svg_rgba", _fake_rasterize)

    _ = frame_cache._rasterize_svg_rgba(svg_path="logo.svg", width_px=80, height_px=40)

    assert calls == [(320, 160)]
    assert fake.resize_calls == [(80, 40)]
