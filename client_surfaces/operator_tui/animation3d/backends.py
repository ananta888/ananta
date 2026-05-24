from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.geometry import build_a_letter, build_snake_ribbon
from client_surfaces.operator_tui.animation3d.models import (
    BackendCapabilities,
    FrameResult,
    GeometryModel,
    LogoAnimationBackend,
    Vertex,
)
from client_surfaces.operator_tui.animation3d.presets import AnimationPreset, builtin_presets


def _rotate_x(v: Vertex, a: float) -> Vertex:
    c, s = math.cos(a), math.sin(a)
    return Vertex(v.x, v.y * c - v.z * s, v.y * s + v.z * c)


def _rotate_y(v: Vertex, a: float) -> Vertex:
    c, s = math.cos(a), math.sin(a)
    return Vertex(v.x * c + v.z * s, v.y, -v.x * s + v.z * c)


def _rotate_z(v: Vertex, a: float) -> Vertex:
    c, s = math.cos(a), math.sin(a)
    return Vertex(v.x * c - v.y * s, v.x * s + v.y * c, v.z)


def _apply_perspective(v: Vertex, d: float) -> tuple[float, float, float]:
    if d <= 0:
        return (v.x, v.y, v.z)
    factor = d / (d + v.z + d * 0.5)
    return (v.x * factor, v.y * factor, v.z)


def _bresenham_line(
    x0: float, y0: float, z0: float,
    x1: float, y1: float, z1: float,
) -> list[tuple[int, int, float]]:
    ix0, iy0 = int(round(x0)), int(round(y0))
    ix1, iy1 = int(round(x1)), int(round(y1))
    dx = abs(ix1 - ix0)
    dy = -abs(iy1 - iy0)
    sx = 1 if ix0 < ix1 else -1
    sy = 1 if iy0 < iy1 else -1
    err = dx + dy

    x, y = ix0, iy0
    total_len = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2) or 1.0
    dist_to_point = 0.0
    total_dist = math.sqrt((ix1 - ix0) ** 2 + (iy1 - iy0) ** 2)
    points: list[tuple[int, int, float]] = []

    while True:
        frac = dist_to_point / total_dist if total_dist > 0 else 0.0
        z = z0 + (z1 - z0) * frac
        points.append((x, y, z))
        if x == ix1 and y == iy1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
        dist_to_point = math.sqrt((x - ix0) ** 2 + (y - iy0) ** 2)

    return points


_SNAKE_CHARS = "sSoOcC~"


class BuiltinBackend:
    def __init__(self) -> None:
        self._a_model = build_a_letter()
        self._snake_model = build_snake_ribbon()
        self._all_models: dict[str, GeometryModel] = {
            "A": self._a_model,
            "snake": self._snake_model,
        }

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_3d=True,
            max_fps=60,
            color_modes=("truecolor", "ansi_256", "mono", "plain_ascii"),
            preset_names=tuple(builtin_presets.keys()),
            description="Built-in pseudo-3D ASCII/ANSI renderer",
        )

    def frame_at(
        self,
        t: float,
        width: int,
        height: int,
        options: dict | None = None,
    ) -> FrameResult:
        opts = options or {}
        preset_name = opts.get("preset", "rotate_in")
        color_mode: str = opts.get("color_mode", "truecolor")
        no_color: bool = opts.get("no_color", color_mode in ("mono", "plain_ascii"))
        no_ansi: bool = opts.get("no_ansi", color_mode == "plain_ascii")

        preset = builtin_presets.get(preset_name, builtin_presets["rotate_in"])

        if width < 80 or height < 18:
            return FrameResult(
                text="",
                visible_width=0,
                visible_height=0,
                ansi_used=False,
                fallback_reason="too_small",
            )

        angle_y = math.pi * 0.4 + preset.rotation_speed * t * math.pi * 2.0
        angle_x = math.pi * 0.15 + preset.rotation_speed * t * math.pi * 0.3
        angle_z = preset.rotation_speed * t * math.pi * 0.1

        scale_factor = preset.scale_at(t)
        perspective_dist = 8.0

        view_center_x = width / 2.0
        view_center_y = height / 2.0
        char_aspect = 0.45

        projected_a = self._project_model(
            self._a_model,
            angle_x, angle_y, angle_z,
            scale_factor, perspective_dist,
            view_center_x, view_center_y, char_aspect,
        )

        snake_phase = preset.snake_phase_offset * t
        projected_snake = self._project_model(
            self._snake_model,
            angle_x, angle_y + snake_phase, angle_z,
            scale_factor, perspective_dist,
            view_center_x, view_center_y, char_aspect,
        )

        frame = self._rasterize(
            projected_a, projected_snake,
            width, height,
            no_color=no_color,
            no_ansi=no_ansi,
            snake_color=preset.snake_color,
            a_color=preset.a_color,
        )

        return FrameResult(
            text=frame,
            visible_width=width,
            visible_height=height,
            ansi_used=not no_ansi,
        )

    def _project_model(
        self,
        model: GeometryModel,
        ax: float, ay: float, az: float,
        scale: float,
        perspective_dist: float,
        cx: float, cy: float,
        aspect: float,
    ) -> list[dict]:
        rotated: list[Vertex] = []
        for v in model.vertices:
            r = _rotate_z(_rotate_y(_rotate_x(v, ax), ay), az)
            r = Vertex(r.x * scale, r.y * scale, r.z * scale)
            p = _apply_perspective(r, perspective_dist)
            px = p[0] * aspect + cx
            py = -p[1] + cy
            rotated.append(Vertex(px, py, p[2]))

        results: list[dict] = []
        for edge in model.edges:
            s = rotated[edge.start]
            e = rotated[edge.end]
            results.append({
                "sx": s.x, "sy": s.y, "sz": s.z,
                "ex": e.x, "ey": e.y, "ez": e.z,
                "part_id": edge.part_id,
            })
        return results

    def _rasterize(
        self,
        a_edges: list[dict],
        snake_edges: list[dict],
        width: int,
        height: int,
        no_color: bool = False,
        no_ansi: bool = False,
        snake_color: str = "",
        a_color: str = "",
    ) -> str:
        grid: list[list[dict]] = [
            [{"z": -1e9, "part": "", "edge_angle": 0.0} for _ in range(width)]
            for _ in range(height)
        ]

        a_edge_angle = 0.0
        for edge in a_edges:
            dx = edge["ex"] - edge["sx"]
            dy = edge["ey"] - edge["sy"]
            a_edge_angle = math.atan2(dy, dx) if (dx != 0 or dy != 0) else 0.0
            self._draw_edge_bresenham(grid, edge, "A", a_edge_angle)

        for edge in snake_edges:
            dx = edge["ex"] - edge["sx"]
            dy = edge["ey"] - edge["sy"]
            angle = math.atan2(dy, dx) if (dx != 0 or dy != 0) else 0.0
            self._draw_edge_bresenham(grid, edge, "snake", angle)

        filled_cells = sum(
            1 for y in range(height) for x in range(width)
            if grid[y][x]["z"] >= -1e8
        )

        if filled_cells < 4:
            return "\n".join(" " * width for _ in range(height))

        lines: list[str] = []
        for y in range(height):
            line_parts: list[str] = []
            last_seg: str | None = None
            for x in range(width):
                cell = grid[y][x]
                if cell["z"] < -1e8:
                    if last_seg is not None:
                        line_parts.append("\x1b[0m")
                        last_seg = None
                    line_parts.append(" ")
                    continue

                ch = self._pick_char(cell)
                seg = cell["part"]

                if not no_ansi and not no_color:
                    color = snake_color if "snake" in seg else a_color
                    esc = self._ansi_color_escape(color)
                    if seg != last_seg:
                        if last_seg is not None:
                            line_parts.append("\x1b[0m")
                        line_parts.append(esc)
                        last_seg = seg
                    line_parts.append(ch)
                else:
                    if last_seg is not None:
                        line_parts.append("\x1b[0m")
                        last_seg = None
                    line_parts.append(ch)

            if last_seg is not None:
                line_parts.append("\x1b[0m")
            lines.append("".join(line_parts))
        return "\n".join(lines)

    def _draw_edge_bresenham(
        self,
        grid: list[list[dict]],
        edge: dict,
        part_prefix: str,
        angle: float,
    ) -> None:
        sx, sy, sz = edge["sx"], edge["sy"], edge["sz"]
        ex, ey, ez = edge["ex"], edge["ey"], edge["ez"]
        part_id = edge["part_id"]
        seg = f"{part_prefix}:{part_id}"
        h = len(grid)
        w = len(grid[0])

        points = _bresenham_line(sx, sy, sz, ex, ey, ez)
        for ix, iy, z in points:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = ix + dx, iy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        cell = grid[ny][nx]
                        if z > cell["z"]:
                            grid[ny][nx] = {"z": z, "part": seg, "edge_angle": angle}

    def _pick_char(self, cell: dict) -> str:
        angle = cell["edge_angle"]
        part = cell["part"]

        if "snake" in part:
            n = len(_SNAKE_CHARS) - 1
            idx = min(n, max(0, int((angle / math.pi + 0.5) * n)))
            return _SNAKE_CHARS[idx]

        norm = (angle + math.pi) % (math.pi * 2)
        if norm < math.pi / 8 or norm >= 15 * math.pi / 8:
            return "-"
        if norm < 3 * math.pi / 8:
            return "/"
        if norm < 5 * math.pi / 8:
            return "|"
        if norm < 7 * math.pi / 8:
            return "\\"
        if norm < 9 * math.pi / 8:
            return "-"
        if norm < 11 * math.pi / 8:
            return "/"
        if norm < 13 * math.pi / 8:
            return "|"
        return "\\"

    def _ansi_color_escape(self, color_spec: str) -> str:
        if not color_spec:
            return ""
        parts = color_spec.split(",")
        if len(parts) == 3:
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            return f"\x1b[38;2;{r};{g};{b}m"
        named = {
            "green": "\x1b[32m",
            "red": "\x1b[31m",
            "yellow": "\x1b[33m",
            "cyan": "\x1b[36m",
            "white": "\x1b[37m",
        }
        return named.get(color_spec, "\x1b[37m")
