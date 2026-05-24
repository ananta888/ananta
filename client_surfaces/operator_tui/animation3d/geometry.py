from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.models import Edge, GeometryModel, Vertex


def _subdivide(vertices: tuple[Vertex, ...], edges: tuple[Edge, ...], segments: int = 4) -> tuple[tuple[Vertex, ...], tuple[Edge, ...]]:
    new_verts: list[Vertex] = list(vertices)
    new_edges: list[Edge] = []
    for edge in edges:
        v1 = vertices[edge.start]
        v2 = vertices[edge.end]
        start_idx = len(new_verts)
        for i in range(1, segments):
            frac = i / segments
            new_verts.append(Vertex(
                x=v1.x + (v2.x - v1.x) * frac,
                y=v1.y + (v2.y - v1.y) * frac,
                z=v1.z + (v2.z - v1.z) * frac,
            ))
        prev = edge.start
        for i in range(segments):
            nxt = start_idx + i if i < segments - 1 else edge.end
            new_edges.append(Edge(prev, nxt, edge.part_id))
            prev = nxt
    return tuple(new_verts), tuple(new_edges)


def build_a_letter() -> GeometryModel:
    _V = (
        Vertex(-2.0, -6.0, 0.0),    # 0  left foot
        Vertex(-2.8, -5.5, 0.3),    # 1  left foot outer
        Vertex(-0.8, 5.0, 0.0),     # 2  left leg top
        Vertex(-0.4, 5.8, 0.0),     # 3  apex left
        Vertex(0.4, 5.8, 0.0),      # 4  apex right
        Vertex(0.8, 5.0, 0.0),      # 5  right leg top
        Vertex(2.8, -5.5, 0.3),     # 6  right foot outer
        Vertex(2.0, -6.0, 0.0),     # 7  right foot
        Vertex(-2.2, -1.5, 0.0),    # 8  crossbar left
        Vertex(2.2, -1.5, 0.0),     # 9  crossbar right
        Vertex(-1.5, 0.0, 0.5),     # 10 inner left
        Vertex(1.5, 0.0, 0.5),      # 11 inner right
        Vertex(0.0, 5.0, 0.0),      # 12 apex center
        Vertex(-2.4, -5.8, -0.2),   # 13 left foot inner
        Vertex(2.4, -5.8, -0.2),    # 14 right foot inner
        Vertex(-0.6, -1.5, -1.0),   # 15 crossbar back left
        Vertex(0.6, -1.5, -1.0),    # 16 crossbar back right
    )

    _E = (
        Edge(0, 1, "left_leg"),
        Edge(1, 2, "left_leg"),
        Edge(2, 3, "left_leg"),
        Edge(3, 12, "apex"),
        Edge(12, 4, "apex"),
        Edge(4, 5, "right_leg"),
        Edge(5, 6, "right_leg"),
        Edge(6, 7, "right_leg"),
        Edge(7, 14, "right_leg"),
        Edge(14, 0, "left_leg"),
        Edge(13, 0, "left_leg"),
        Edge(2, 13, "left_leg"),
        Edge(8, 9, "crossbar"),
        Edge(9, 16, "crossbar"),
        Edge(16, 15, "crossbar"),
        Edge(15, 8, "crossbar"),
        Edge(2, 8, "left_leg"),
        Edge(5, 9, "right_leg"),
        Edge(10, 11, "inner_cutout"),
        Edge(3, 4, "apex"),
    )

    _V, _E = _subdivide(_V, _E, 4)
    _V, _E = _subdivide(_V, _E, 3)

    return GeometryModel(
        vertices=_V,
        edges=_E,
        part_ids=("left_leg", "right_leg", "crossbar", "apex", "inner_cutout"),
        label="ananta_A",
    )


def build_snake_ribbon(num_points: int = 96) -> GeometryModel:
    verts: list[Vertex] = []
    edges: list[Edge] = []

    for i in range(num_points):
        angle = (i / num_points) * 2.0 * math.pi
        t = i / num_points

        y = -6.5 + t * 13.0
        radius = 1.8 + 0.8 * math.sin(angle * 2.0)

        wrap_x = radius * math.sin(angle * 0.8 + 0.5)
        wrap_z = radius * math.cos(angle * 0.8 + 0.5)

        x_offset = 3.0 * math.sin(angle * 1.5)
        z_offset = 3.0 * math.cos(angle * 1.5)

        verts.append(Vertex(
            x=wrap_x + x_offset * 0.3,
            y=y,
            z=wrap_z + z_offset * 0.3 + 0.5 * math.sin(t * math.pi),
        ))

    for i in range(num_points):
        part = "snake_head" if i < 3 else ("snake_tail" if i > num_points - 4 else "snake_body")
        edges.append(Edge(i, (i + 1) % num_points, part))

    return GeometryModel(
        vertices=tuple(verts),
        edges=tuple(edges),
        part_ids=("snake_head", "snake_body", "snake_tail"),
        label="ananta_snake",
    )
