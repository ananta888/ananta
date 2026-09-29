from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.pixel_geometry import (
    fit_frame_size_to_terminal,
    header_logo_target_pixels,
    map_cells_to_pixels,
)


def test_map_cells_to_pixels_uses_default_cell_ratio() -> None:
    width_px, height_px = map_cells_to_pixels(120, 40)
    assert width_px == 960
    assert height_px == 640


def test_fit_frame_size_to_terminal_returns_sensible_default() -> None:
    width_px, height_px = fit_frame_size_to_terminal(columns=120, rows=40, quality="interactive")
    assert (width_px, height_px) == (480, 270)


def test_header_logo_target_pixels_not_tied_to_line_count() -> None:
    width_px, height_px = header_logo_target_pixels(width_cells=80, height_lines=8, oversampling=2)
    assert width_px >= 160
    assert height_px >= 16
