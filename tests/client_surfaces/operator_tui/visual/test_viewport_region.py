from __future__ import annotations

import pytest

from client_surfaces.operator_tui.visual.viewport.center_viewport import CenterViewport
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion, derive_pixel_size


def test_viewport_region_validates_sizes() -> None:
    with pytest.raises(ValueError):
        ViewportRegion(x=0, y=0, columns=0, rows=10, pixel_width=800, pixel_height=450)
    with pytest.raises(ValueError):
        ViewportRegion(x=0, y=0, columns=10, rows=10, pixel_width=0, pixel_height=450)


def test_derive_pixel_size_uses_defaults_when_terminal_geometry_unknown() -> None:
    w, h = derive_pixel_size(
        columns=40,
        rows=12,
        default_pixel_width=800,
        default_pixel_height=450,
        max_pixel_width=1280,
        max_pixel_height=720,
    )
    assert (w, h) == (800, 450)


def test_derive_pixel_size_clamps_to_max_limits() -> None:
    w, h = derive_pixel_size(
        columns=200,
        rows=60,
        terminal_pixel_width=4000,
        terminal_pixel_height=2000,
        terminal_columns=200,
        terminal_rows=60,
        max_pixel_width=1280,
        max_pixel_height=720,
    )
    assert (w, h) == (1280, 720)


def test_center_viewport_keeps_region_with_draw_requests() -> None:
    viewport = CenterViewport(minimum_columns=8, minimum_rows=4)
    region = ViewportRegion(x=24, y=9, columns=50, rows=16, pixel_width=800, pixel_height=450)
    viewport.resize(region)
    draw_request = viewport.make_draw_request({"frame_id": "demo"})
    assert draw_request.region == region
    assert draw_request.payload["frame_id"] == "demo"


def test_center_viewport_reports_small_region_fallback_mode() -> None:
    viewport = CenterViewport(minimum_columns=8, minimum_rows=4)
    region = ViewportRegion(x=24, y=9, columns=6, rows=3, pixel_width=200, pixel_height=120)
    state = viewport.resize(region)
    assert state.is_too_small_for_image_protocols is True
    assert "viewport too small" in state.reason

