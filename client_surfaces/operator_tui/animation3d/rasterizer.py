from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.models import GeometryModel, Vertex


@staticmethod
def depth_at_z(z: float, model: GeometryModel) -> float:
    zs = [v.z for v in model.vertices]
    if not zs:
        return 0.5
    mn, mx = min(zs), max(zs)
    span = mx - mn
    if span < 1e-9:
        return 0.5
    return (z - mn) / span


class DepthGrid:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._grid: list[list[dict]] = [
            [{"z": -1e9, "part": "", "angle": 0.0, "is_snake": False}
             for _ in range(width)]
            for _ in range(height)
        ]

    def draw_line(
        self,
        sx: float, sy: float, sz: float,
        ex: float, ey: float, ez: float,
        part_id: str,
        is_snake: bool = False,
    ) -> None:
        dx = ex - sx
        dy = ey - sy
        dz = ez - sz
        steps = max(1, int(math.sqrt(dx * dx + dy * dy) * 2.0))
        angle = math.atan2(dy, dx) if steps > 0 else 0.0

        for i in range(steps + 1):
            frac = i / steps
            px = sx + dx * frac
            py = sy + dy * frac
            pz = sz + dz * frac
            ix = int(round(px))
            iy = int(round(py))
            if 0 <= ix < self.width and 0 <= iy < self.height:
                cell = self._grid[iy][ix]
                if pz > cell["z"]:
                    self._grid[iy][ix] = {
                        "z": pz,
                        "part": part_id,
                        "angle": angle,
                        "is_snake": is_snake,
                    }

    def draw_model(
        self,
        model: GeometryModel,
        vertices: list[Vertex],
        is_snake: bool = False,
    ) -> None:
        for edge in model.edges:
            s = vertices[edge.start]
            e = vertices[edge.end]
            self.draw_line(
                s.x, s.y, s.z,
                e.x, e.y, e.z,
                edge.part_id,
                is_snake=is_snake,
            )

    def iter_cells(self):
        for y in range(self.height):
            for x in range(self.width):
                yield x, y, self._grid[y][x]

    def cell_at(self, x: int, y: int) -> dict:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self._grid[y][x]
        return {"z": -1e9, "part": "", "angle": 0.0, "is_snake": False}

    def cells_by_row(self, y: int):
        if 0 <= y < self.height:
            for x in range(self.width):
                yield x, self._grid[y][x]
