"""Unicode block art renderer: converts PNG bytes to ANSI text lines.

Uses ▀ (upper half block) with 24-bit foreground/background colors so each
terminal character represents 2 pixels vertically. Works in any True-Color
terminal (xterm-256color, xterm-direct, etc.) without Kitty or Sixel.
"""
from __future__ import annotations

import io

_R = "\033[0m"


def png_to_block_art(
    png_bytes: bytes,
    *,
    max_cols: int = 80,
    max_rows: int = 20,
    bg_r: int = 12,
    bg_g: int = 20,
    bg_b: int = 28,
) -> list[str]:
    """Convert PNG bytes to a list of ANSI block-art lines.

    Each line uses ▀ glyphs with 24-bit foreground (top pixel) and
    background (bottom pixel) colors. Returns empty list when Pillow
    is unavailable or the image cannot be decoded.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return []

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return []

    # Scale image to fit the terminal area (1 col = 1 pixel wide, 2 pixels tall)
    px_w = max(1, max_cols)
    px_h = max(1, max_rows * 2)

    orig_w, orig_h = img.size
    if orig_w > 0 and orig_h > 0:
        scale = min(px_w / orig_w, px_h / orig_h, 1.0)
        new_w = max(1, int(orig_w * scale))
        new_h = max(1, int(orig_h * scale))
        if new_w != orig_w or new_h != orig_h:
            img = img.resize((new_w, new_h), Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.ANTIALIAS)  # type: ignore[attr-defined]

    w, h = img.size
    pixels = img.load()
    lines: list[str] = []

    for row in range(0, h, 2):
        parts: list[str] = []
        for col in range(w):
            # Top pixel
            tp = pixels[col, row]
            tr, tg, tb = tp[0], tp[1], tp[2]
            ta = tp[3] if len(tp) > 3 else 255

            # Bottom pixel (may be out of bounds → use bg color)
            if row + 1 < h:
                bp = pixels[col, row + 1]
                br, bg_c, bb = bp[0], bp[1], bp[2]
                ba = bp[3] if len(bp) > 3 else 255
            else:
                br, bg_c, bb, ba = bg_r, bg_g, bg_b, 255

            # Blend with background for transparent pixels
            def _blend(c: int, a: int, bg: int) -> int:
                return int(c * a / 255 + bg * (255 - a) / 255)

            tr = _blend(tr, ta, bg_r)
            tg = _blend(tg, ta, bg_g)
            tb = _blend(tb, ta, bg_b)
            br = _blend(br, ba, bg_r)
            bg_c = _blend(bg_c, ba, bg_g)
            bb = _blend(bb, ba, bg_b)

            parts.append(
                f"\033[38;2;{tr};{tg};{tb}m"
                f"\033[48;2;{br};{bg_c};{bb}m▀"
            )
        lines.append("".join(parts) + _R)

    return lines


def _svg_natural_size(svg_bytes: bytes) -> tuple[int, int] | None:
    """Extract natural width/height from SVG viewBox or width/height attributes."""
    import re
    text = svg_bytes[:2000].decode("utf-8", errors="replace")
    vb = re.search(r'viewBox=["\'][\s0-9.]*?[\s]+([\d.]+)[\s]+([\d.]+)["\']', text)
    if vb:
        w, h = float(vb.group(1)), float(vb.group(2))
        if w > 0 and h > 0:
            return int(w), int(h)
    mw = re.search(r'max-width:\s*([\d.]+)px', text)
    if mw:
        w = float(mw.group(1))
        if w > 0:
            return int(w), int(w * 0.6)
    return None


def svg_to_block_art(
    svg_bytes: bytes,
    *,
    max_cols: int = 80,
    max_rows: int = 20,
) -> list[str]:
    """Convert SVG bytes to block art via cairosvg → PNG → block art.

    Renders at native SVG resolution (or a scaled-up version for small SVGs),
    then downscales to terminal block-art dimensions. This preserves sharpness
    for wide diagrams instead of pre-scaling to a tiny pixel grid.
    """
    try:
        import cairosvg  # type: ignore

        natural = _svg_natural_size(svg_bytes)
        if natural:
            nat_w, nat_h = natural
            # For very small SVGs, render at 2× to get crisper pixels
            if nat_w < 200:
                out_w = nat_w * 2
                out_h = nat_h * 2
            else:
                # Render at native size; png_to_block_art will downscale
                out_w = nat_w
                out_h = nat_h
            png_bytes = cairosvg.svg2png(
                bytestring=svg_bytes,
                output_width=max(4, out_w),
                output_height=max(4, out_h),
            )
        else:
            # No size info: render at native
            png_bytes = cairosvg.svg2png(bytestring=svg_bytes)

        return png_to_block_art(png_bytes, max_cols=max_cols, max_rows=max_rows)
    except Exception:
        return []
