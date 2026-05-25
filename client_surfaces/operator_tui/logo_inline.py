from __future__ import annotations

import math
import os
import sys
import tempfile
from functools import lru_cache
from typing import Any

_SVG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "ananta.svg")
)
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
)

_RST = "\x1b[0m"
_BG_THRESHOLD = 28


def _fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def _bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


# ── shared image loading ───────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def _load_logo_image(pixel_w: int, pixel_h: int) -> Any | None:
    """Load, crop and resize the SVG logo to pixel_w × pixel_h. Cached."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    try:
        from render_terminal_logo import svg_to_png
        from PIL import Image
    except ImportError:
        return None
    if not os.path.isfile(_SVG_PATH):
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        png_path = f.name
    try:
        svg_to_png(_SVG_PATH, png_path, width=max(600, pixel_w * 10))
        img = Image.open(png_path).convert("RGBA")
    except Exception:
        return None
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass

    # Detect background from corners
    src_w, src_h = img.size
    corners = [img.getpixel((x, y)) for x, y in
               [(0, 0), (src_w - 1, 0), (0, src_h - 1), (src_w - 1, src_h - 1)]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def is_bg(r: int, g: int, b: int) -> bool:
        return ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5 < _BG_THRESHOLD

    # Crop to logo bounding box
    px = img.load()
    min_x, min_y, max_x, max_y = src_w, src_h, 0, 0
    for y in range(src_h):
        for x in range(src_w):
            r, g, b, _ = px[x, y]  # type: ignore[index]
            if not is_bg(r, g, b):
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)

    if max_x > min_x and max_y > min_y:
        pad = max((max_x - min_x) // 20, 2)
        img = img.crop((
            max(0, min_x - pad), max(0, min_y - pad),
            min(src_w, max_x + pad), min(src_h, max_y + pad),
        ))

    img = img.resize((pixel_w, pixel_h), Image.LANCZOS)
    # Attach background detector to image object for callers
    img._is_bg = is_bg  # type: ignore[attr-defined]
    return img


# ── Braille renderer (drawille — 2×4 px per char, 100×32 in 50×8) ─────────────

def render_logo_braille(cols: int = 50, rows: int = 8) -> list[str] | None:
    """
    Render SVG logo via drawille Braille characters.
    Resolution: cols*2 × rows*4 pixels (100×32 at 50×8 chars).
    Returns None if drawille or the SVG pipeline is unavailable.
    """
    try:
        return _cached_braille(cols, rows)
    except Exception:
        return None


def render_logo_braille_animated(
    cols: int = 50,
    rows: int = 8,
    *,
    t: float = 0.0,
    speed: float = 1.2,
    pulse_depth: float = 0.14,
    shimmer_depth: float = 0.18,
) -> list[str] | None:
    """Animate original SVG braille render with subtle pulse/shimmer color modulation."""
    try:
        frame_rows, char_colors = _cached_braille_cells(cols, rows)
    except Exception:
        return None
    if frame_rows is None:
        return None

    pulse = 1.0 + pulse_depth * math.sin(t * speed * math.tau)
    result: list[str] = []
    for row_i in range(rows):
        raw = frame_rows[row_i] if row_i < len(frame_rows) else ""
        line = ""
        for col_i in range(cols):
            ch = raw[col_i] if col_i < len(raw) else " "
            color = char_colors.get((col_i, row_i))
            if color and ch != " ":
                wave = 1.0 + shimmer_depth * math.sin((col_i * 0.33) + (row_i * 0.71) + t * speed * 1.8)
                factor = max(0.62, min(1.42, pulse * wave))
                r, g, b = color
                rr = max(0, min(255, int(r * factor)))
                gg = max(0, min(255, int(g * factor)))
                bb = max(0, min(255, int(b * factor)))
                line += f"{_fg(rr, gg, bb)}{ch}{_RST}"
            else:
                line += " "
        result.append(line)
    return result


@lru_cache(maxsize=8)
def _cached_braille(cols: int, rows: int) -> list[str] | None:
    cells = _cached_braille_cells(cols, rows)
    if cells is None:
        return None
    frame_rows, char_colors = cells
    return _compose_braille_lines(frame_rows, char_colors, cols, rows)


@lru_cache(maxsize=8)
def _cached_braille_cells(cols: int, rows: int) -> tuple[tuple[str, ...], dict[tuple[int, int], tuple[int, int, int]]] | None:
    try:
        import drawille
    except ImportError:
        return None

    img = _load_logo_image(cols * 2, rows * 4)
    if img is None:
        return None

    is_bg = img._is_bg  # type: ignore[attr-defined]
    px = img.load()

    canvas = drawille.Canvas()
    char_colors: dict[tuple[int, int], tuple[int, int, int]] = {}

    for cy in range(rows):
        for cx in range(cols):
            lit: list[tuple[int, int, int]] = []
            for dy in range(4):
                for dx in range(2):
                    r, g, b, _ = px[cx * 2 + dx, cy * 4 + dy]  # type: ignore[index]
                    if not is_bg(r, g, b):
                        canvas.set(cx * 2 + dx, cy * 4 + dy)
                        lit.append((r, g, b))
            if lit:
                char_colors[(cx, cy)] = tuple(
                    sum(c[i] for c in lit) // len(lit) for i in range(3)
                )  # type: ignore[assignment]

    return (tuple(canvas.rows()), char_colors)


def _compose_braille_lines(
    frame_rows: tuple[str, ...],
    char_colors: dict[tuple[int, int], tuple[int, int, int]],
    cols: int,
    rows: int,
) -> list[str]:
    result: list[str] = []
    for row_i in range(rows):
        raw = frame_rows[row_i] if row_i < len(frame_rows) else ""
        line = ""
        for col_i in range(cols):
            ch = raw[col_i] if col_i < len(raw) else " "
            color = char_colors.get((col_i, row_i))
            if color and ch != " ":
                r, g, b = color
                line += f"{_fg(r, g, b)}{ch}{_RST}"
            else:
                line += " "
        result.append(line)
    return result


# ── Half-block renderer (fallback — 2 px per char row) ────────────────────────

def render_logo_halfblock(cols: int = 50, rows: int = 8) -> list[str] | None:
    """
    Render SVG logo as Unicode half-block art (▀ / ▄).
    Resolution: cols × rows*2 pixels.
    Returns None if unavailable.
    """
    try:
        return _cached_halfblock(cols, rows)
    except Exception:
        return None


def render_logo_snake_game_animated(
    cols: int = 50,
    rows: int = 8,
    *,
    t: float = 0.0,
    speed: float = 1.5,
) -> list[str] | None:
    """Idle animation: simple A-shape with moving snake and random inner gate."""
    board_w = max(12, min(24, cols - 2))
    board_h = max(6, min(10, rows))
    geom = build_a_snake_geometry(board_w, board_h, seed=int(t * speed * 100))
    snake_base, snake_head = _snake_palette_from_svg(cols, rows)

    grid_chars = [[" " for _ in range(cols)] for _ in range(rows)]
    grid_colors: list[list[tuple[int, int, int] | None]] = [[None for _ in range(cols)] for _ in range(rows)]

    pulse = 1.0 + 0.08 * math.sin(t * speed * math.tau)
    wall_col = (
        max(0, min(255, int(snake_base[0] * pulse))),
        max(0, min(255, int(snake_base[1] * pulse))),
        max(0, min(255, int(snake_base[2] * pulse))),
    )
    gate_col = _lerp_color(snake_head, (255, 220, 120), 0.45)
    for wx, wy in geom["walls"]:
        mapped = _map_board_cell_to_canvas(wx, wy, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            continue
        mx, my = mapped
        grid_chars[my][mx] = "█"
        grid_colors[my][mx] = wall_col

    gate = geom.get("gate")
    if isinstance(gate, tuple) and len(gate) == 2:
        mapped_gate = _map_board_cell_to_canvas(gate[0], gate[1], board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped_gate is not None:
            gx, gy = mapped_gate
            grid_chars[gy][gx] = "◌"
            grid_colors[gy][gx] = gate_col

    ring = _snake_ring_path(1, 1, max(2, board_w - 2), max(2, board_h - 2))
    if not ring:
        return None
    head_index = int(t * speed * 9) % len(ring)
    snake_len = max(6, min(16, len(ring) // 3))
    for i in range(snake_len):
        idx = (head_index - i) % len(ring)
        sx, sy = ring[idx]
        mapped = _map_board_cell_to_canvas(sx, sy, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            continue
        mx, my = mapped
        mix = i / max(1, snake_len - 1)
        col = _lerp_color(snake_head, snake_base, mix)
        ch = "●" if i == 0 else ("◉" if i < 4 else "·")
        grid_chars[my][mx] = ch
        grid_colors[my][mx] = col

    return _compose_color_grid(grid_chars, grid_colors, rows=rows)


def render_logo_snake_game_playable(
    cols: int = 50,
    rows: int = 8,
    *,
    game_state: dict[str, Any] | None = None,
    t: float = 0.0,
    speed: float = 1.2,
) -> list[str] | None:
    """Render playable snake on a simple A logo with a randomly opening inner gap."""
    if not game_state:
        return None

    body = game_state.get("snake") or []
    food = game_state.get("food")
    board_w = max(1, int(game_state.get("board_w", 18)))
    board_h = max(1, int(game_state.get("board_h", 6)))
    alive = bool(game_state.get("alive", True))
    if not body:
        return None

    snake_base, snake_head = _snake_palette_from_svg(cols, rows)
    gate_seed = int(game_state.get("gate_seed", int(t * 1000)))
    geom = build_a_snake_geometry(board_w, board_h, seed=gate_seed)

    grid_chars = [[" " for _ in range(cols)] for _ in range(rows)]
    grid_colors: list[list[tuple[int, int, int] | None]] = [[None for _ in range(cols)] for _ in range(rows)]

    # Base "A" logo
    pulse = 1.0 + 0.06 * math.sin(t * speed * math.tau)
    wall_col = (
        max(0, min(255, int(snake_base[0] * pulse))),
        max(0, min(255, int(snake_base[1] * pulse))),
        max(0, min(255, int(snake_base[2] * pulse))),
    )
    for wx, wy in geom["walls"]:
        mapped = _map_board_cell_to_canvas(wx, wy, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            continue
        mx, my = mapped
        grid_chars[my][mx] = "█"
        grid_colors[my][mx] = wall_col

    gate = geom.get("gate")
    if isinstance(gate, tuple) and len(gate) == 2:
        mapped_gate = _map_board_cell_to_canvas(gate[0], gate[1], board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped_gate is not None:
            gx, gy = mapped_gate
            grid_chars[gy][gx] = "◌"
            grid_colors[gy][gx] = _lerp_color(snake_head, (255, 220, 120), 0.4)

    portals = game_state.get("portals")
    if isinstance(portals, dict):
        portal_colors = {
            "nav": _lerp_color(snake_head, (120, 220, 255), 0.45),
            "content": _lerp_color(snake_head, (255, 210, 120), 0.35),
            "detail": _lerp_color(snake_head, (220, 160, 255), 0.35),
            "command": _lerp_color(snake_head, (255, 255, 180), 0.45),
        }
        portal_chars = {"nav": "▷", "content": "▽", "detail": "◇", "command": "◎"}
        for key, pos in portals.items():
            if key not in portal_chars or not isinstance(pos, (list, tuple)) or len(pos) != 2:
                continue
            mapped_portal = _map_board_cell_to_canvas(
                int(pos[0]),
                int(pos[1]),
                board_w=board_w,
                board_h=board_h,
                cols=cols,
                rows=rows,
            )
            if mapped_portal is None:
                continue
            px, py = mapped_portal
            grid_chars[py][px] = portal_chars[key]
            grid_colors[py][px] = portal_colors[key]

    # Snake body
    for idx, logical in enumerate(body):
        if not isinstance(logical, (list, tuple)) or len(logical) != 2:
            continue
        sx, sy = int(logical[0]), int(logical[1])
        mapped = _map_board_cell_to_canvas(sx, sy, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            continue
        mx, my = mapped
        mix = idx / max(1, len(body) - 1)
        col = _lerp_color(snake_head, snake_base, mix)
        if idx == 0:
            ch = "●" if alive else "✕"
        elif idx < 4:
            ch = "◉"
        elif idx < 8:
            ch = "◍"
        else:
            ch = "·"
        grid_chars[my][mx] = ch
        grid_colors[my][mx] = col

    # Food
    if isinstance(food, (list, tuple)) and len(food) == 2:
        mapped_food = _map_board_cell_to_canvas(
            int(food[0]),
            int(food[1]),
            board_w=board_w,
            board_h=board_h,
            cols=cols,
            rows=rows,
        )
        if mapped_food is not None:
            fx, fy = mapped_food
            # warmer accent from SVG palette by shifting from head color
            warm = (
                min(255, int(snake_head[0] * 1.10)),
                min(255, int(snake_head[1] * 0.95)),
                min(255, int(snake_head[2] * 0.85)),
            )
            grid_chars[fy][fx] = "◆"
            grid_colors[fy][fx] = warm

    return _compose_color_grid(grid_chars, grid_colors, rows=rows)


def build_a_snake_geometry(board_w: int, board_h: int, seed: int = 0) -> dict[str, object]:
    board_w = max(10, board_w)
    board_h = max(6, board_h)
    apex_x = board_w // 2
    left_base_x = 2
    right_base_x = board_w - 3
    cross_y = max(2, min(board_h - 3, board_h // 2))

    walls: set[tuple[int, int]] = set()
    interior: set[tuple[int, int]] = set()
    left_edge: dict[int, int] = {}
    right_edge: dict[int, int] = {}

    for y in range(board_h):
        if board_h <= 1:
            frac = 0.0
        else:
            frac = y / (board_h - 1)
        lx = round(apex_x + (left_base_x - apex_x) * frac)
        rx = round(apex_x + (right_base_x - apex_x) * frac)
        if lx > rx:
            lx, rx = rx, lx
        left_edge[y] = lx
        right_edge[y] = rx
        walls.add((lx, y))
        walls.add((rx, y))
        for x in range(lx + 1, rx):
            if y > 0:
                interior.add((x, y))

    cl = left_edge.get(cross_y, left_base_x)
    cr = right_edge.get(cross_y, right_base_x)
    for x in range(cl + 1, cr):
        walls.add((x, cross_y))
        interior.discard((x, cross_y))

    gate_candidates = [(x, cross_y) for x in range(cl + 2, cr - 1)]
    gate: tuple[int, int] | None = None
    if gate_candidates:
        gate = gate_candidates[seed % len(gate_candidates)]
        walls.discard(gate)

    return {"walls": walls, "interior": interior, "gate": gate}


def _map_board_cell_to_canvas(
    x: int,
    y: int,
    *,
    board_w: int,
    board_h: int,
    cols: int,
    rows: int,
) -> tuple[int, int] | None:
    if board_w <= 0 or board_h <= 0:
        return None
    if not (0 <= x < board_w and 0 <= y < board_h):
        return None
    left = 1 if cols > 6 else 0
    top = 0
    usable_w = max(1, cols - left - 1)
    usable_h = max(1, rows - top)
    mx = left + round((x / max(1, board_w - 1)) * max(0, usable_w - 1))
    my = top + round((y / max(1, board_h - 1)) * max(0, usable_h - 1))
    return mx, my


def _compose_color_grid(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    rows: int,
) -> list[str]:
    lines: list[str] = []
    for y in range(rows):
        line = ""
        for x in range(len(grid_chars[y])):
            ch = grid_chars[y][x]
            col = grid_colors[y][x]
            if col is None or ch == " ":
                line += " "
            else:
                line += f"{_fg(col[0], col[1], col[2])}{ch}{_RST}"
        lines.append(line)
    return lines


def _snake_palette_from_svg(cols: int, rows: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    cached = _cached_logo_cells(cols, rows)
    if cached is not None:
        _cells, _bbox, snake_base, snake_head = cached
        return snake_base, snake_head
    return (70, 188, 132), (186, 244, 214)


def _snake_ring_path(left: int, top: int, right: int, bottom: int) -> list[tuple[int, int]]:
    if right <= left or bottom <= top:
        return []
    path: list[tuple[int, int]] = []
    for x in range(left, right + 1):
        path.append((x, top))
    for y in range(top + 1, bottom + 1):
        path.append((right, y))
    for x in range(right - 1, left - 1, -1):
        path.append((x, bottom))
    for y in range(bottom - 1, top, -1):
        path.append((left, y))
    return path


@lru_cache(maxsize=8)
def _cached_logo_cells(
    cols: int, rows: int
) -> tuple[
    dict[tuple[int, int], tuple[int, int, int]],
    tuple[int, int, int, int],
    tuple[int, int, int],
    tuple[int, int, int],
] | None:
    img = _load_logo_image(cols, rows * 2)
    if img is None:
        return None
    is_bg = img._is_bg  # type: ignore[attr-defined]
    px = img.load()

    cells: dict[tuple[int, int], tuple[int, int, int]] = {}
    min_x, min_y, max_x, max_y = cols, rows, 0, 0
    swatch: list[tuple[int, int, int]] = []
    for y in range(rows):
        for x in range(cols):
            lit: list[tuple[int, int, int]] = []
            for dy in (0, 1):
                r, g, b, _ = px[x, y * 2 + dy]  # type: ignore[index]
                if not is_bg(r, g, b):
                    lit.append((r, g, b))
                    swatch.append((r, g, b))
            if lit:
                c = (
                    sum(p[0] for p in lit) // len(lit),
                    sum(p[1] for p in lit) // len(lit),
                    sum(p[2] for p in lit) // len(lit),
                )
                cells[(x, y)] = c
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if not cells:
        return None

    # pick snake palette from svg colors (green-ish body, bright head)
    snake_base = max(swatch, key=lambda c: (c[1] - (c[0] + c[2]) * 0.35, c[1]))
    snake_head = max(swatch, key=lambda c: (c[0] + c[1] + c[2], c[1]))
    return cells, (min_x, min_y, max_x, max_y), snake_base, snake_head


def render_logo_halfblock_animated(
    cols: int = 50,
    rows: int = 8,
    *,
    t: float = 0.0,
    speed: float = 1.2,
    pulse_depth: float = 0.10,
    shimmer_depth: float = 0.12,
) -> list[str] | None:
    """Animate SVG half-block render when drawille is unavailable."""
    try:
        img = _load_logo_image(cols, rows * 2)
    except Exception:
        return None
    if img is None:
        return None

    is_bg = img._is_bg  # type: ignore[attr-defined]
    px = img.load()

    pulse = 1.0 + pulse_depth * math.sin(t * speed * math.tau)
    lines: list[str] = []
    for row in range(rows):
        line = ""
        for col in range(cols):
            r1, g1, b1, _ = px[col, row * 2]      # type: ignore[index]
            r2, g2, b2, _ = px[col, row * 2 + 1]  # type: ignore[index]
            top = not is_bg(r1, g1, b1)
            bot = not is_bg(r2, g2, b2)
            if not top and not bot:
                line += " "
                continue

            wave = 1.0 + shimmer_depth * math.sin((col * 0.25) + (row * 0.9) + t * speed * 1.6)
            factor = max(0.70, min(1.35, pulse * wave))

            def mod(c: int) -> int:
                return max(0, min(255, int(c * factor)))

            if not top:
                line += f"{_fg(mod(r2), mod(g2), mod(b2))}▄{_RST}"
            elif not bot:
                line += f"{_fg(mod(r1), mod(g1), mod(b1))}▀{_RST}"
            else:
                line += (
                    f"{_fg(mod(r1), mod(g1), mod(b1))}"
                    f"{_bg(mod(r2), mod(g2), mod(b2))}▀{_RST}"
                )
        lines.append(line)
    return lines


@lru_cache(maxsize=8)
def _cached_halfblock(cols: int, rows: int) -> list[str] | None:
    img = _load_logo_image(cols, rows * 2)
    if img is None:
        return None

    is_bg = img._is_bg  # type: ignore[attr-defined]
    px = img.load()

    lines: list[str] = []
    for row in range(rows):
        line = ""
        for col in range(cols):
            r1, g1, b1, _ = px[col, row * 2]      # type: ignore[index]
            r2, g2, b2, _ = px[col, row * 2 + 1]  # type: ignore[index]
            top = not is_bg(r1, g1, b1)
            bot = not is_bg(r2, g2, b2)
            if not top and not bot:
                line += " "
            elif not top:
                line += f"{_fg(r2, g2, b2)}▄{_RST}"
            elif not bot:
                line += f"{_fg(r1, g1, b1)}▀{_RST}"
            else:
                line += f"{_fg(r1, g1, b1)}{_bg(r2, g2, b2)}▀{_RST}"
        lines.append(line)

    return lines
