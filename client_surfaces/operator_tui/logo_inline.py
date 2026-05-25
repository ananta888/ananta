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
    """Idle animation: empty area with gently circling snake."""
    board_w = max(12, min(24, cols - 2))
    board_h = max(6, min(10, rows))
    snake_base, snake_head = _snake_palette_from_svg(cols, rows)

    grid_chars = [[" " for _ in range(cols)] for _ in range(rows)]
    grid_colors: list[list[tuple[int, int, int] | None]] = [[None for _ in range(cols)] for _ in range(rows)]

    ring = _snake_ring_path(max(1, board_w // 3), max(1, board_h // 3), max(2, (board_w * 2) // 3), max(2, (board_h * 2) // 3))
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
    """Render playable snake only (left-top area stays clean, no box clutter)."""
    if not game_state:
        return None

    body = game_state.get("snake") or []
    snakes_raw = game_state.get("snakes") if isinstance(game_state.get("snakes"), dict) else {}
    food = game_state.get("food")
    board_w = max(1, int(game_state.get("board_w", 18)))
    board_h = max(1, int(game_state.get("board_h", 6)))
    active = bool(game_state.get("active"))
    alive = bool(game_state.get("alive", True))
    if not body and not snakes_raw:
        return None

    snake_base, snake_head = _snake_palette_from_svg(cols, rows)

    grid_chars = [[" " for _ in range(cols)] for _ in range(rows)]
    grid_colors: list[list[tuple[int, int, int] | None]] = [[None for _ in range(cols)] for _ in range(rows)]
    if active:
        _draw_active_mode_frame(grid_chars, grid_colors, cols=cols, rows=rows, t=t, color_base=snake_head)

    if active and body:
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

    if bool(game_state.get("active")):
        _draw_snake_mode_legend(grid_chars, grid_colors, cols=cols, rows=rows, color=snake_head)
    elif snakes_raw:
        _draw_passive_snake_roster(
            grid_chars,
            grid_colors,
            cols=cols,
            rows=rows,
            snakes=snakes_raw,
            local_snake_id=str(game_state.get("local_snake_id") or "s1"),
        )

    return _compose_color_grid(grid_chars, grid_colors, rows=rows)


def _draw_passive_snake_roster(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    cols: int,
    rows: int,
    snakes: dict[str, Any],
    local_snake_id: str,
) -> None:
    if cols < 18 or rows < 3:
        return
    ordered = sorted(
        ((str(k), v) for k, v in snakes.items() if isinstance(v, dict)),
        key=lambda item: (0 if item[0] == local_snake_id else 1, item[0]),
    )
    max_rows = max(1, rows - 1)
    for idx, (sid, snap) in enumerate(ordered[: max_rows]):
        y = 1 + idx
        if y >= rows:
            break
        color_name = str(snap.get("snake_color") or "mint")
        col = _logo_snake_palette(color_name)
        pseudonym = str(snap.get("pseudonym") or sid)
        label = f"{sid.upper()} {pseudonym} [{color_name}]"
        for j, ch in enumerate(label[: max(0, cols - 2)]):
            x = 1 + j
            if x >= cols:
                break
            grid_chars[y][x] = ch
            grid_colors[y][x] = col


def _logo_snake_palette(name: str) -> tuple[int, int, int]:
    palettes = {
        "mint": (170, 255, 210),
        "cyan": (120, 235, 255),
        "violet": (212, 176, 255),
        "amber": (255, 205, 130),
        "rose": (255, 170, 200),
    }
    return palettes.get(name, palettes["mint"])


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


def build_snake_control_boxes(
    board_w: int,
    board_h: int,
    *,
    section_ids: tuple[str, ...] | list[str],
) -> list[dict[str, object]]:
    boxes: list[dict[str, object]] = []
    board_w = max(8, board_w)
    board_h = max(4, board_h)

    panes = ("navigation", "content", "detail", "command")
    pane_h = 2 if board_h >= 6 else 1
    for i, target in enumerate(panes):
        x0 = round((i * board_w) / len(panes))
        x1 = round(((i + 1) * board_w) / len(panes)) - 1
        boxes.append(
            {
                "kind": "pane",
                "target": target,
                "label": target[:3].upper() if target != "command" else "INP",
                "x0": max(0, x0),
                "y0": 0,
                "x1": max(0, min(board_w - 1, x1)),
                "y1": pane_h - 1,
            }
        )

    section_list = [str(s) for s in section_ids] or ["dashboard"]
    y0 = pane_h
    section_h = max(1, board_h - y0)
    cols = min(5, max(1, len(section_list)))
    rows = max(1, (len(section_list) + cols - 1) // cols)
    for idx, sid in enumerate(section_list):
        r = min(rows - 1, idx // cols)
        c = idx % cols
        sx0 = round((c * board_w) / cols)
        sx1 = round(((c + 1) * board_w) / cols) - 1
        sy0 = y0 + round((r * section_h) / rows)
        sy1 = y0 + round(((r + 1) * section_h) / rows) - 1
        boxes.append(
            {
                "kind": "section",
                "target": sid,
                "label": sid[:3].upper(),
                "x0": max(0, sx0),
                "y0": max(y0, sy0),
                "x1": max(0, min(board_w - 1, sx1)),
                "y1": max(y0, min(board_h - 1, sy1)),
            }
        )
    return boxes


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


def _draw_active_mode_frame(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    cols: int,
    rows: int,
    t: float,
    color_base: tuple[int, int, int],
) -> None:
    if cols < 3 or rows < 3:
        return
    pulse = 1.0 + 0.18 * math.sin(t * math.tau * 1.2)
    col = (
        max(0, min(255, int(color_base[0] * pulse))),
        max(0, min(255, int(color_base[1] * pulse))),
        max(0, min(255, int(color_base[2] * pulse))),
    )
    spikes = ("*", "✶", "·", "•")
    phase = int(t * 20)

    def paint(x: int, y: int, ch: str) -> None:
        if 0 <= x < cols and 0 <= y < rows:
            grid_chars[y][x] = ch
            grid_colors[y][x] = col

    top, bottom = 0, rows - 1
    left, right = 0, cols - 1
    for x in range(left, right + 1):
        ch = spikes[(x + phase) % len(spikes)] if (x + phase) % 5 == 0 else "─"
        paint(x, top, ch)
        ch2 = spikes[(x + phase + 1) % len(spikes)] if (x + phase + 2) % 5 == 0 else "─"
        paint(x, bottom, ch2)
    for y in range(top, bottom + 1):
        ch = spikes[(y + phase) % len(spikes)] if (y + phase + 1) % 5 == 0 else "│"
        paint(left, y, ch)
        ch2 = spikes[(y + phase + 2) % len(spikes)] if (y + phase + 3) % 5 == 0 else "│"
        paint(right, y, ch2)
    paint(left, top, "┌")
    paint(right, top, "┐")
    paint(left, bottom, "└")
    paint(right, bottom, "┘")


def _draw_snake_mode_legend(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    cols: int,
    rows: int,
    color: tuple[int, int, int],
) -> None:
    if cols < 28 or rows < 4:
        return

    legend = (
        "SNAKE MODE [Ctrl+S off]",
        "Keys: arrows move/boost, space stop",
        "B frame-mode, X set/add frame selection",
        "X select, C copy, V replace (cmdline), Z clear",
        "U tutorial-ai snake parallel an/aus",
        "T text-style, Y color, top-left label always on",
        "M message, Enter save, Esc cancel",
    )
    start_x = 2
    start_y = 1
    tint = (
        max(0, min(255, int(color[0] * 0.95))),
        max(0, min(255, int(color[1] * 0.95))),
        max(0, min(255, int(color[2] * 0.95))),
    )

    for i, text in enumerate(legend):
        y = start_y + i
        if y >= rows - 1:
            break
        for j, ch in enumerate(text):
            x = start_x + j
            if x >= cols - 1:
                break
            grid_chars[y][x] = ch
            grid_colors[y][x] = tint


def _a_blue_from_svg(cols: int, rows: int) -> tuple[int, int, int]:
    cached = _cached_logo_cells(cols, rows)
    if cached is not None:
        cells, _bbox, _snake_base, _snake_head = cached
        if cells:
            ranked = sorted(cells.values(), key=lambda c: (c[2] - (c[0] + c[1]) * 0.45, c[2]), reverse=True)
            if ranked:
                return ranked[0]
    return (74, 136, 228)


def _zone_palette() -> dict[str, tuple[int, int, int]]:
    return {
        "nav": (98, 212, 255),
        "content": (255, 200, 96),
        "detail": (214, 156, 255),
        "command": (255, 238, 150),
        "section": (120, 190, 255),
    }


def _draw_zone_frames(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    board_w: int,
    board_h: int,
    cols: int,
    rows: int,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    third = max(1, board_w // 3)
    zones = (
        ("nav", 0, min(board_w - 1, third - 1)),
        ("content", min(board_w - 1, third), min(board_w - 1, third * 2 - 1)),
        ("detail", min(board_w - 1, third * 2), board_w - 1),
    )

    def paint(bx: int, by: int, ch: str, color: tuple[int, int, int]) -> None:
        mapped = _map_board_cell_to_canvas(bx, by, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            return
        x, y = mapped
        grid_chars[y][x] = ch
        grid_colors[y][x] = color

    top = 0
    bottom = max(0, board_h - 1)
    for key, x0, x1 in zones:
        if x1 < x0:
            continue
        color = palette[key]
        for x in range(x0, x1 + 1):
            paint(x, top, "─", color)
            paint(x, bottom, "─", color)
        for y in range(top, bottom + 1):
            paint(x0, y, "│", color)
            paint(x1, y, "│", color)
        paint(x0, top, "┌", color)
        paint(x1, top, "┐", color)
        paint(x0, bottom, "└", color)
        paint(x1, bottom, "┘", color)


def _draw_control_boxes(
    grid_chars: list[list[str]],
    grid_colors: list[list[tuple[int, int, int] | None]],
    *,
    boxes: list[dict[str, object]],
    board_w: int,
    board_h: int,
    cols: int,
    rows: int,
) -> None:
    palette = _zone_palette()

    def paint(bx: int, by: int, ch: str, color: tuple[int, int, int]) -> None:
        mapped = _map_board_cell_to_canvas(bx, by, board_w=board_w, board_h=board_h, cols=cols, rows=rows)
        if mapped is None:
            return
        x, y = mapped
        grid_chars[y][x] = ch
        grid_colors[y][x] = color

    for box in boxes:
        kind = str(box.get("kind", "section"))
        target = str(box.get("target", ""))
        label = str(box.get("label", ""))
        x0 = int(box.get("x0", 0))
        y0 = int(box.get("y0", 0))
        x1 = int(box.get("x1", x0))
        y1 = int(box.get("y1", y0))
        if x1 < x0 or y1 < y0:
            continue
        if kind == "pane":
            color = palette.get(target if target != "navigation" else "nav", palette["section"])
        else:
            color = palette["section"]
        for x in range(x0, x1 + 1):
            paint(x, y0, "─", color)
            paint(x, y1, "─", color)
        for y in range(y0, y1 + 1):
            paint(x0, y, "│", color)
            paint(x1, y, "│", color)
        paint(x0, y0, "┌", color)
        paint(x1, y0, "┐", color)
        paint(x0, y1, "└", color)
        paint(x1, y1, "┘", color)
        if label and x1 - x0 >= 2 and y1 - y0 >= 0:
            ly = (y0 + y1) // 2
            lx = x0 + max(1, ((x1 - x0 + 1) - len(label)) // 2)
            for i, ch in enumerate(label[: max(1, x1 - x0 - 1)]):
                paint(min(x1 - 1, lx + i), ly, ch, color)


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
