from __future__ import annotations

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


@lru_cache(maxsize=8)
def _cached_braille(cols: int, rows: int) -> list[str] | None:
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

    frame_rows = canvas.rows()

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
