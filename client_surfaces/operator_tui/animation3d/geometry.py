from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.models import Edge, GeometryModel, Vertex


def build_a_letter() -> GeometryModel:
    _V = [
        Vertex(-2.0, -6.0, 0.0),    # 0  left foot
        Vertex(-2.8, -5.5, 0.3),    # 1  left foot outer
        Vertex(-0.8, 5.0, 0.0),     # 2  left leg top
        Vertex(-1.2, 5.5, 0.0),     # 3  apex left
        Vertex(1.2, 5.5, 0.0),      # 4  apex right
        Vertex(0.8, 5.0, 0.0),      # 5  right leg top
        Vertex(2.8, -5.5, 0.3),     # 6  right foot outer
        Vertex(2.0, -6.0, 0.0),     # 7  right foot
        Vertex(-2.2, -1.5, 0.0),    # 8  crossbar left
        Vertex(2.2, -1.5, 0.0),     # 9  crossbar right
        Vertex(-1.5, 0.0, 0.5),     # 10 inner left
        Vertex(1.5, 0.0, 0.5),      # 11 inner right
        Vertex(0.0, 5.0, 0.0),      # 12 apex center
    ]

    _E = [
        Edge(0, 1, "left_leg"),
        Edge(1, 2, "left_leg"),
        Edge(2, 3, "left_leg"),
        Edge(3, 12, "apex"),
        Edge(12, 4, "apex"),
        Edge(4, 5, "right_leg"),
        Edge(5, 6, "right_leg"),
        Edge(6, 7, "right_leg"),
        Edge(8, 9, "crossbar"),
        Edge(2, 8, "left_leg"),
        Edge(5, 9, "right_leg"),
        Edge(10, 11, "inner_cutout"),
    ]

    return GeometryModel(
        vertices=_V,
        edges=_E,
        part_ids=("left_leg", "right_leg", "crossbar", "apex", "inner_cutout"),
        label="ananta_A",
    )


def build_snake_ribbon(num_points: int = 48) -> GeometryModel:
    verts: list[Vertex] = []
    edges: list[Edge] = []
    part_ids = ("snake_head", "snake_body", "snake_tail")

    for i in range(num_points):
        angle = (i / num_points) * 2.0 * math.pi
        t = i / num_points

        y = -5.5 + t * 11.0
        radius = 1.5 + 0.8 * math.sin(angle * 2.0)

        x_offset = 2.5 * math.sin(angle * 1.5)
        z_offset = 2.5 * math.cos(angle * 1.5)

        wrap_x = radius * math.sin(angle * 0.7 + 0.5)
        wrap_z = radius * math.cos(angle * 0.7 + 0.5)

        verts.append(Vertex(
            x=wrap_x + x_offset * 0.3,
            y=y,
            z=wrap_z + z_offset * 0.3 + 0.5 * math.sin(t * math.pi),
        ))

    for i in range(num_points - 1):
        part = "snake_head" if i < 3 else ("snake_tail" if i > num_points - 5 else "snake_body")
        edges.append(Edge(i, i + 1, part))

    edges.append(Edge(num_points - 1, 0, "snake_tail"))

    return GeometryModel(
        vertices=tuple(verts),
        edges=tuple(edges),
        part_ids=part_ids,
        label="ananta_snake",
    )
