from __future__ import annotations

from client_surfaces.operator_tui.animation3d.geometry import build_a_letter, build_snake_ribbon


class TestALetterGeometry:
    def test_has_vertices(self):
        model = build_a_letter()
        assert len(model.vertices) >= 12
        assert model.label == "ananta_A"

    def test_has_edges(self):
        model = build_a_letter()
        assert len(model.edges) >= 10

    def test_has_part_ids(self):
        model = build_a_letter()
        assert "left_leg" in model.part_ids
        assert "right_leg" in model.part_ids
        assert "crossbar" in model.part_ids

    def test_centered_around_origin(self):
        model = build_a_letter()
        xs = [v.x for v in model.vertices]
        ys = [v.y for v in model.vertices]
        assert min(xs) < -1.0
        assert max(xs) > 1.0
        assert min(ys) < -5.0
        assert max(ys) > 5.0

    def test_scale(self):
        model = build_a_letter()
        scaled = model.scale(2.0)
        assert abs(scaled.vertices[0].x - model.vertices[0].x * 2.0) < 0.01


class TestSnakeRibbonGeometry:
    def test_has_vertices(self):
        model = build_snake_ribbon()
        assert len(model.vertices) >= 24
        assert model.label == "ananta_snake"

    def test_has_edges(self):
        model = build_snake_ribbon()
        assert len(model.edges) == len(model.vertices)

    def test_has_part_ids(self):
        model = build_snake_ribbon()
        assert "snake_head" in model.part_ids
        assert "snake_body" in model.part_ids
        assert "snake_tail" in model.part_ids

    def test_z_depth_varies(self):
        model = build_snake_ribbon()
        zs = [v.z for v in model.vertices]
        assert max(zs) - min(zs) > 1.0
