from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache

_SVG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "ananta.svg")
)
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
)

_RST = "\x1b[0m"


def _fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def _bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"


def render_logo_halfblock(cols: int = 35, rows: int = 8) -> list[str] | None:
    """
    Render the SVG logo as Unicode half-block art.

    Each character cell holds two pixel rows (▀ upper / ▄ lower half-block),
    so the effective pixel resolution is cols × (rows * 2).
    Returns None if PIL or the SVG/PNG pipeline is unavailable.
    """
    try:
        return _cached_render(cols, rows)
    except Exception:
        return None


@lru_cache(maxsize=8)
def _cached_render(cols: int, rows: int) -> list[str] | None:
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)

    try:
        from render_terminal_logo import svg_to_png
    except ImportError:
        return None

    if not os.path.isfile(_SVG_PATH):
        return None

    try:
        from PIL import Image
    except ImportError:
        return None

    # Render SVG at a generous resolution, then downscale with LANCZOS
    pixel_w = cols
    pixel_h = rows * 2  # two pixel rows per character row

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        png_path = f.name
    try:
        # Wide render preserves SVG detail before downscale
        svg_to_png(_SVG_PATH, png_path, width=max(400, pixel_w * 12))
        img = Image.open(png_path).convert("RGBA")
    except Exception:
        return None
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass

    # Detect background color from image corners (SVG has explicit white bg)
    src_w, src_h = img.size
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((src_w - 1, 0)),
        img.getpixel((0, src_h - 1)),
        img.getpixel((src_w - 1, src_h - 1)),
    ]
    bg_r = sum(c[0] for c in corners) // 4
    bg_g = sum(c[1] for c in corners) // 4
    bg_b = sum(c[2] for c in corners) // 4
    _BG_THRESHOLD = 28

    def _is_bg(r: int, g: int, b: int) -> bool:
        return ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5 < _BG_THRESHOLD

    # Crop to bounding box of non-background pixels so the logo fills the frame
    pixels = img.load()
    min_x, min_y, max_x, max_y = src_w, src_h, 0, 0
    for y in range(src_h):
        for x in range(src_w):
            r, g, b, _ = pixels[x, y]  # type: ignore[index]
            if not _is_bg(r, g, b):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x > min_x and max_y > min_y:
        pad = max((max_x - min_x) // 20, 2)
        min_x = max(0, min_x - pad)
        min_y = max(0, min_y - pad)
        max_x = min(src_w, max_x + pad)
        max_y = min(src_h, max_y + pad)
        img = img.crop((min_x, min_y, max_x, max_y))

    # Downscale to target pixel grid
    img = img.resize((pixel_w, pixel_h), Image.LANCZOS)

    lines: list[str] = []
    for row in range(rows):
        line = ""
        for col in range(pixel_w):
            r1, g1, b1, _ = img.getpixel((col, row * 2))
            r2, g2, b2, _ = img.getpixel((col, row * 2 + 1))

            top = not _is_bg(r1, g1, b1)
            bot = not _is_bg(r2, g2, b2)

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
