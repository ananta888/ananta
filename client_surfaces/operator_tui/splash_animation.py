from __future__ import annotations

import os
import re
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
_GREEN = '\x1b[38;2;71;166;56m'
_RESET_C = '\x1b[0m'
_SNAKE_CHARS = '~sSoOcC'


def _render_svg_ascii(logo_width: int, color: bool = True) -> list[str]:
    import tempfile
    scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from render_terminal_logo import (
            ASCII_PALETTES, RenderConfig, load_image,
            render_ascii, render_ascii_color, svg_to_png,
        )
    except ImportError:
        return []

    svg_path = os.path.abspath(os.path.join(scripts_dir, '..', 'ananta.svg'))
    if not os.path.isfile(svg_path):
        return []

    cfg = RenderConfig()
    chars = ASCII_PALETTES['clean']

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        png = f.name
    try:
        svg_to_png(svg_path, png, width=800)
        img = load_image(png, logo_width, cfg)
        art = render_ascii_color(img, chars) if color else render_ascii(img, chars, cfg)
    except Exception:
        return []
    finally:
        try:
            os.unlink(png)
        except OSError:
            pass

    return art.split('\n')


def _pad_line(line: str, width: int) -> str:
    visible = len(_ANSI_RE.sub('', line))
    if visible < width:
        return line + ' ' * (width - visible)
    return line


def _orbit_path(top: int, bottom: int, left: int, right: int) -> list[tuple[int, int]]:
    p: list[tuple[int, int]] = []
    for c in range(left, right + 1):          p.append((top, c))
    for r in range(top + 1, bottom + 1):      p.append((r, right))
    for c in range(right - 1, left - 1, -1):  p.append((bottom, c))
    for r in range(bottom - 1, top, -1):       p.append((r, left))
    return p


def _colorize_region(chars_list: list[str], snake_set: dict[int, str], col_offset: int) -> str:
    out, in_g = '', False
    for i, ch in enumerate(chars_list):
        col = i + col_offset
        if col in snake_set:
            if not in_g:
                out += _GREEN
                in_g = True
            out += ch
        else:
            if in_g:
                out += _RESET_C
                in_g = False
            out += ch
    if in_g:
        out += _RESET_C
    return out


def _insert_snake_on_row(
    base: str,
    snake_cols: dict[int, str],
    logo_col_start: int,
    canvas_w: int,
) -> str:
    if not snake_cols:
        return base.ljust(canvas_w)

    left_snakes  = {c: ch for c, ch in snake_cols.items() if c < logo_col_start}
    right_snakes = {c: ch for c, ch in snake_cols.items() if c >= logo_col_start + 70}

    if not left_snakes and not right_snakes:
        return base.ljust(canvas_w)

    left_part = list(' ' * logo_col_start)
    for c, ch in left_snakes.items():
        if 0 <= c < logo_col_start:
            left_part[c] = ch

    left_str = _colorize_region(left_part, left_snakes, 0)
    logo_str = base[logo_col_start:] if len(base) > logo_col_start else ''

    logo_end = logo_col_start + 70
    right_len = canvas_w - logo_end
    right_part = list(' ' * right_len)
    for c, ch in right_snakes.items():
        idx = c - logo_end
        if 0 <= idx < right_len:
            right_part[idx] = ch

    right_str = _colorize_region(
        right_part,
        {c - logo_end: ch for c, ch in right_snakes.items() if 0 <= c - logo_end < right_len},
        0,
    )

    return left_str + logo_str + right_str


def _frame_from_logo(
    logo_lines: list[str],
    logo_col: int,
    canvas_w: int,
    canvas_h: int,
    n_logo_rows: int,
    snake_map: dict[tuple[int, int], str],
) -> str:
    rows_out: list[str] = []
    logo_h = min(n_logo_rows, len(logo_lines))

    for row in range(canvas_h):
        if row < logo_h:
            base = ' ' * logo_col + logo_lines[row]
        else:
            base = ''

        row_snakes = {c: ch for (r, c), ch in snake_map.items() if r == row}

        if not row_snakes:
            rows_out.append(_pad_line(base, canvas_w) if base else ' ' * canvas_w)
        elif row >= logo_h:
            parts, in_g = '', False
            for ci in range(canvas_w):
                if ci in row_snakes:
                    if not in_g:
                        parts += _GREEN
                        in_g = True
                    parts += row_snakes[ci]
                else:
                    if in_g:
                        parts += _RESET_C
                        in_g = False
                    parts += ' '
            if in_g:
                parts += _RESET_C
            rows_out.append(parts)
        else:
            rows_out.append(_insert_snake_on_row(base, row_snakes, logo_col, canvas_w))

    return '\n'.join(rows_out)


def build_splash_frames(w: int = 120, h: int = 32, fps: int = 24) -> list[str]:
    """
    SVG logo reveals line-by-line → green snake grows and orbits → shrinks to corner.
    Returns list of frame strings with ANSI color codes. Empty if SVG render unavailable.
    """
    logo_lg = _render_svg_ascii(70, color=True)
    logo_sm = _render_svg_ascii(35, color=False)

    if not logo_lg:
        return []

    logo_w = 70
    col0 = (w - logo_w) // 2

    content_rows = [i for i, ln in enumerate(logo_lg) if _ANSI_RE.sub('', ln).strip()]
    if not content_rows:
        content_rows = list(range(len(logo_lg)))
    ct, cb = content_rows[0], content_rows[-1]

    pad_r, pad_c = 2, 3
    orbit_top    = max(0,     ct - pad_r)
    orbit_bottom = min(h - 1, cb + pad_r)
    orbit_left   = max(0,     col0 - pad_c)
    orbit_right  = min(w - 1, col0 + logo_w + pad_c)
    path = _orbit_path(orbit_top, orbit_bottom, orbit_left, orbit_right)
    plen = len(path)

    frames: list[str] = []

    # phase 1: logo reveals line-by-line (0.5 s)
    for fi in range(max(1, fps // 2)):
        n = int((fi + 1) / max(1, fps // 2) * len(logo_lg))
        frames.append(_frame_from_logo(logo_lg, col0, w, h, n, {}))

    # phase 2: snake grows around logo (1.5 s)
    grow = int(fps * 1.5)
    for fi in range(grow):
        n_s = int((fi + 1) / grow * plen)
        snake_map = {path[k]: _SNAKE_CHARS[(k + fi) % len(_SNAKE_CHARS)] for k in range(n_s)}
        frames.append(_frame_from_logo(logo_lg, col0, w, h, len(logo_lg), snake_map))

    # phase 3: full snake orbits slowly (1 s)
    for fi in range(fps):
        off = fi * 2
        snake_map = {path[(off + k) % plen]: _SNAKE_CHARS[(k + off) % len(_SNAKE_CHARS)]
                     for k in range(plen)}
        frames.append(_frame_from_logo(logo_lg, col0, w, h, len(logo_lg), snake_map))

    # phase 4: shrink toward top-left (1 s)
    last_off = fps * 2
    col_sm = (w - 35) // 2
    for fi in range(fps):
        t = fi / fps
        if t < 0.5:
            art, a_col = logo_lg, col0
        else:
            art, a_col = (logo_sm if logo_sm else logo_lg), (col_sm if logo_sm else col0)
        target_col = max(0, a_col - int(a_col * t * 2))
        snake_visible = int(plen * max(0.0, 1.0 - t * 2))
        snake_map = {path[(last_off + k) % plen]: _SNAKE_CHARS[(k + last_off) % len(_SNAKE_CHARS)]
                     for k in range(snake_visible)}
        frames.append(_frame_from_logo(art, target_col, w, h, len(art), snake_map))

    return frames
