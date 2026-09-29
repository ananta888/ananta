from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.models import Vertex
from client_surfaces.operator_tui.animation3d.projection import (
    orthographic,
    perspective,
    rotate_x,
    rotate_y,
    rotate_z,
)


class TestRotation:
    def test_rotate_x_identity(self):
        v = Vertex(1.0, 0.0, 0.0)
        r = rotate_x(v, 0.0)
        assert abs(r.x - 1.0) < 1e-9
        assert abs(r.y) < 1e-9

    def test_rotate_y_90(self):
        v = Vertex(1.0, 0.0, 0.0)
        r = rotate_y(v, math.pi / 2)
        assert abs(r.x) < 1e-9
        assert abs(r.z + 1.0) < 1e-9

    def test_rotate_z_180(self):
        v = Vertex(1.0, 0.0, 0.0)
        r = rotate_z(v, math.pi)
        assert abs(r.x + 1.0) < 1e-9
        assert abs(r.y) < 1e-9


class TestProjection:
    def test_orthographic_preserves_coords(self):
        sx, sy, sz = orthographic(Vertex(2.0, 3.0, 4.0))
        assert sx == 2.0
        assert sy == 3.0
        assert sz == 4.0

    def test_perspective_far_is_smaller(self):
        near = perspective(Vertex(1.0, 1.0, 0.0), d=8.0)
        far = perspective(Vertex(1.0, 1.0, 8.0), d=8.0)
        assert abs(far[0]) < abs(near[0])
        assert abs(far[1]) < abs(near[1])

    def test_perspective_at_infinity(self):
        far = perspective(Vertex(1.0, 1.0, 1000.0), d=8.0)
        # factor = 8/(1000+8) ~ 0.0079
        assert abs(far[0]) < 0.01
        assert abs(far[1]) < 0.01
