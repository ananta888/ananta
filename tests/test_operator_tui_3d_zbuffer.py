from __future__ import annotations

from client_surfaces.operator_tui.animation3d.rasterizer import DepthGrid


class TestDepthGrid:
    def test_cell_defaults_to_empty(self):
        grid = DepthGrid(80, 24)
        cell = grid.cell_at(10, 10)
        assert cell["z"] < -1e8
        assert cell["part"] == ""

    def test_draw_line_sets_cells(self):
        grid = DepthGrid(80, 24)
        grid.draw_line(10.0, 10.0, 0.0, 20.0, 10.0, 0.0, "test_part")
        cell = grid.cell_at(15, 10)
        assert cell["z"] > -1e8
        assert cell["part"] == "test_part"

    def test_closer_z_overwrites(self):
        grid = DepthGrid(80, 24)
        grid.draw_line(10.0, 10.0, 0.0, 20.0, 10.0, 0.0, "far_part")
        grid.draw_line(10.0, 10.0, 5.0, 20.0, 10.0, 5.0, "near_part")
        cell = grid.cell_at(15, 10)
        assert cell["part"] == "near_part"

    def test_farther_does_not_overwrite(self):
        grid = DepthGrid(80, 24)
        grid.draw_line(10.0, 10.0, 5.0, 20.0, 10.0, 5.0, "near_part")
        grid.draw_line(10.0, 10.0, 0.0, 20.0, 10.0, 0.0, "far_part")
        cell = grid.cell_at(15, 10)
        assert cell["part"] == "near_part"

    def test_out_of_bounds_is_safe(self):
        grid = DepthGrid(80, 24)
        grid.draw_line(-100.0, -100.0, 0.0, -50.0, -50.0, 0.0, "outside")
        cell = grid.cell_at(0, 0)
        assert cell["z"] < -1e8
