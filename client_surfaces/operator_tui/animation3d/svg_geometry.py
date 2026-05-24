"""
Build a GeometryModel from the project's SVG logo.

Renders the SVG to a bitmap via cairosvg, extracts the logo's silhouette
boundary, orders the boundary pixels by polar angle, maps them to 3D
coordinates, and assigns Z-depth and part_id from the original pixel colors.

Part-id convention (ties into BuiltinBackend._rasterize color logic):
  "logo_a"      → a_color  (dark-blue regions)
  "logo_snake"  → snake_color (green regions, "snake" substring triggers it)
"""
from __future__ import annotations

import math
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from client_surfaces.operator_tui.animation3d.models import GeometryModel


def build_geometry_from_svg(
    svg_path: str,
    n_samples: int = 220,
    render_px: int = 160,
) -> "GeometryModel | None":
    """
    Return a GeometryModel derived from the logo's silhouette, or None if
    cairosvg / PIL are not available.
    """
    try:
        from cairosvg import svg2png
        from PIL import Image
    except ImportError:
        return None

    from client_surfaces.operator_tui.animation3d.models import Edge, GeometryModel, Vertex

    if not os.path.isfile(svg_path):
        return None

    # ── 1. render SVG → temporary PNG ──────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        png_path = f.name
    try:
        svg2png(url=svg_path, write_to=png_path,
                output_width=render_px, output_height=render_px)
        img = Image.open(png_path).convert("RGBA")
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass

    img = img.resize((render_px, render_px), Image.LANCZOS)
    w, h = img.size
    px = list(img.getdata())

    # ── 2. build filled mask and collect per-pixel colors ──────────────────
    filled: set[tuple[int, int]] = set()
    colors: dict[tuple[int, int], tuple[int, int, int]] = {}

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[y * w + x]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if a > 180 and lum < 235:
                filled.add((x, y))
                colors[(x, y)] = (r, g, b)

    # ── 3. find boundary pixels ────────────────────────────────────────────
    boundary: list[tuple[int, int]] = []
    for (x, y) in filled:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if (x + dx, y + dy) not in filled:
                boundary.append((x, y))
                break

    if len(boundary) < 10:
        return None

    # ── 4. order by polar angle from centroid ─────────────────────────────
    cx = sum(p[0] for p in boundary) / len(boundary)
    cy = sum(p[1] for p in boundary) / len(boundary)
    boundary.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

    # ── 5. downsample ──────────────────────────────────────────────────────
    step = max(1, len(boundary) // n_samples)
    sampled = boundary[::step][:n_samples]

    # ── 6. convert to 3D vertices ──────────────────────────────────────────
    scale = 12.0 / max(w, h)      # map render_px coordinates → [-6, 6]
    verts: list[Vertex] = []
    part_ids: list[str] = []

    for (x, y) in sampled:
        nx = (x - cx) * scale
        ny = -(y - cy) * scale    # flip Y (terminal rows increase downward)

        r, g, b = colors.get((x, y), (120, 120, 120))
        # Z-depth and part-id from pixel color:
        #   dark-blue (#043E62): z forward (+) → part "logo_a"
        #   green     (#47A638): z back   (-)  → part "logo_snake" (triggers snake_color)
        if b > r and b > 60 and r < 80:        # blue-dominant
            nz = 0.8
            pid = "logo_a"
        elif g > r * 1.3 and g > 100:          # green-dominant
            nz = -0.4
            pid = "logo_snake"
        else:
            nz = 0.0
            pid = "logo_a"

        verts.append(Vertex(nx, ny, nz))
        part_ids.append(pid)

    n = len(verts)
    if n < 4:
        return None

    # ── 7. build edges ─────────────────────────────────────────────────────
    edges: list[Edge] = []

    # Main silhouette ring: consecutive vertices form the outline loop
    for i in range(n):
        j = (i + 1) % n
        pid = part_ids[i]
        edges.append(Edge(i, j, pid))

    # Structural cross-connections for 3D depth perception (~every 1/6 of loop)
    spoke = max(1, n // 6)
    for i in range(0, n, spoke):
        j = (i + n // 3) % n
        edges.append(Edge(i, j, part_ids[i]))
        k = (i + 2 * n // 3) % n
        edges.append(Edge(i, k, part_ids[i]))

    return GeometryModel(
        vertices=tuple(verts),
        edges=tuple(edges),
        part_ids=("logo_a", "logo_snake"),
        label="ananta_svg",
    )


def _find_svg(start_dir: str | None = None) -> str | None:
    """Walk up the directory tree looking for ananta.svg."""
    here = start_dir or os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        candidate = os.path.join(here, "ananta.svg")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None
