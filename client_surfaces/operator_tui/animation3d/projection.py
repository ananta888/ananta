from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.models import Vertex


def rotate_x(v: Vertex, angle: float) -> Vertex:
    c, s = math.cos(angle), math.sin(angle)
    return Vertex(v.x, v.y * c - v.z * s, v.y * s + v.z * c)


def rotate_y(v: Vertex, angle: float) -> Vertex:
    c, s = math.cos(angle), math.sin(angle)
    return Vertex(v.x * c + v.z * s, v.y, -v.x * s + v.z * c)


def rotate_z(v: Vertex, angle: float) -> Vertex:
    c, s = math.cos(angle), math.sin(angle)
    return Vertex(v.x * c - v.y * s, v.x * s + v.y * c, v.z)


def rotate(v: Vertex, ax: float, ay: float, az: float) -> Vertex:
    return rotate_z(rotate_y(rotate_x(v, ax), ay), az)


def orthographic(v: Vertex) -> tuple[float, float, float]:
    return (v.x, v.y, v.z)


def perspective(v: Vertex, d: float = 8.0) -> tuple[float, float, float]:
    if d <= 0:
        return (v.x, v.y, v.z)
    denom = d + v.z
    if abs(denom) < 1e-9:
        denom = 1e-9
    factor = d / denom
    return (v.x * factor, v.y * factor, v.z)


def map_to_viewport(
    x: float, y: float,
    view_width: int, view_height: int,
    cx: float = 0.0, cy: float = 0.0,
    aspect: float = 0.45,
    scale: float = 1.0,
) -> tuple[int, int]:
    screen_x = int(round(x * aspect * scale + view_width / 2.0 + cx))
    screen_y = int(round(-y * scale + view_height / 2.0 + cy))
    return (screen_x, screen_y)
