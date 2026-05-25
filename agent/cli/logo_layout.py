from __future__ import annotations

import os
import re
import sys
import tempfile
from typing import Sequence

from agent.cli.logo_assets import load_logo, strip_ansi_from_logo
from agent.cli.status_snapshot import StatusSnapshot, format_status_compact_right

COMPACT_HEADER_LINES = 8
_MIN_TERMINAL_WIDTH_FOR_LOGO = 60
_MIN_TERMINAL_WIDTH_FOR_STATUS = 40
_MIN_TERMINAL_WIDTH_FOR_BOTH = 80

# width of the small logo rendered for the persistent TUI header
_SMALL_LOGO_COLS = 35

_small_logo_cache: list[str] | None = None


def _load_small_logo() -> list[str]:
    """Return compact ASCII logo (35 cols, ~8 lines, SVG colors)."""
    global _small_logo_cache
    if _small_logo_cache is not None:
        return _small_logo_cache

    lines = _render_small_logo()
    if not lines:
        lines = _fallback_small_logo()

    # strip blank-only lines top and bottom, then pad to COMPACT_HEADER_LINES
    content = [l for l in lines if l.strip()]
    while len(content) < COMPACT_HEADER_LINES:
        content.append("")
    content = content[:COMPACT_HEADER_LINES]

    _small_logo_cache = content
    return content


def _render_small_logo() -> list[str]:
    """Render ananta.svg at 22 cols using render_terminal_logo; color in dark blue."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    script_dir = os.path.abspath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        from render_terminal_logo import (
            ASCII_PALETTES, RenderConfig, load_image,
            render_ascii, svg_to_png,
        )
    except ImportError:
        return []

    svg_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ananta.svg")
    )
    if not os.path.isfile(svg_path):
        return []

    cfg = RenderConfig()
    chars = ASCII_PALETTES["clean"]
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            png = f.name
        try:
            svg_to_png(svg_path, png, width=800)
            img = load_image(png, _SMALL_LOGO_COLS, cfg)
            art = render_ascii(img, chars, cfg)
        finally:
            try:
                os.unlink(png)
            except OSError:
                pass
    except Exception:
        return []

    raw_lines = art.split("\n")
    # color each non-blank line
    colored = []
    for line in raw_lines:
        if line.strip():
            colored.append(f"{_LOGO_FG}{line}{_ANSI_RST}")
        else:
            colored.append(line)
    return colored


def _fallback_small_logo() -> list[str]:
    """Minimal text fallback when SVG rendering is unavailable."""
    art = [
        "   /\\    ",
        "  /  \\   ",
        " / /\\ \\  ",
        "/______\\ ",
        " ananta  ",
    ]
    return [f"{_LOGO_FG}{l}{_ANSI_RST}" for l in art]


def _visible_length(text: str) -> int:
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    return len(ansi_re.sub("", text))


def _pad_to(text: str, width: int) -> str:
    visible = _visible_length(text)
    if visible >= width:
        return text
    return text + " " * (width - visible)


def _truncate_to(text: str, width: int) -> str:
    visible = _visible_length(text)
    if visible <= width:
        return text
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    parts = ansi_re.split(text)
    ansi_codes = ansi_re.findall(text)
    result = ""
    remaining = width
    for i, part in enumerate(parts):
        if i > 0 and i - 1 < len(ansi_codes):
            result += ansi_codes[i - 1]
        if remaining <= 0:
            break
        take = min(len(part), remaining)
        result += part[:take]
        remaining -= take
    return result


def render_compact_header(
    snapshot: StatusSnapshot | None = None,
    *,
    terminal_width: int | None = None,
    color: bool | None = None,
    prefer_ascii: bool = False,
) -> list[str]:
    if terminal_width is None:
        try:
            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80

    use_color = bool(color) if color is not None else True

    if terminal_width < _MIN_TERMINAL_WIDTH_FOR_STATUS:
        return [""] * COMPACT_HEADER_LINES

    # ── small logo (22 cols, full shape, dark blue) ───────────────────────────
    logo_enabled = os.getenv("ANANTA_TUI_LOGO", "").strip().lower() not in {"0", "false", "no", "off"}
    logo_lines: list[str]
    if logo_enabled and terminal_width >= _MIN_TERMINAL_WIDTH_FOR_LOGO:
        logo_lines = _load_small_logo()
    else:
        logo_lines = [""] * COMPACT_HEADER_LINES

    logo_visual_w = _SMALL_LOGO_COLS if any(l.strip() for l in logo_lines) else 0
    sep = " │ " if logo_visual_w else ""
    right_width = terminal_width - logo_visual_w - len(sep)

    if snapshot is None or terminal_width < _MIN_TERMINAL_WIDTH_FOR_BOTH or right_width < 20:
        return logo_lines[:COMPACT_HEADER_LINES]

    # ── status lines (right of logo) ──────────────────────────────────────────
    from agent.cli.status_snapshot import format_status_lines
    status_lines = format_status_lines(snapshot, color=use_color, width=right_width)
    while len(status_lines) < COMPACT_HEADER_LINES:
        status_lines.append("")

    result = []
    for i in range(COMPACT_HEADER_LINES):
        logo_part   = logo_lines[i]   if i < len(logo_lines)   else ""
        status_part = status_lines[i] if i < len(status_lines) else ""
        padded = _pad_to(logo_part, logo_visual_w)
        result.append(padded + sep + status_part)

    return result


def _max_line_width(lines: Sequence[str]) -> int:
    return max((_visible_length(line) for line in lines), default=0)


def _render_status_only(
    snapshot: StatusSnapshot | None,
    terminal_width: int,
    color: bool | None,
) -> list[str]:
    if snapshot is None:
        return [""] * COMPACT_HEADER_LINES

    lines = format_status_compact_right(
        snapshot,
        color=bool(color),
        right_width=min(terminal_width - 2, 40),
    )
    while len(lines) < COMPACT_HEADER_LINES:
        lines.append("")
    return lines
