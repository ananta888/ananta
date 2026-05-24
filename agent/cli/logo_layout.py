from __future__ import annotations

import re
from typing import Sequence

from agent.cli.logo_assets import load_logo, strip_ansi_from_logo
from agent.cli.status_snapshot import StatusSnapshot, format_status_compact_right

COMPACT_HEADER_LINES = 8
_MIN_TERMINAL_WIDTH_FOR_LOGO = 60
_MIN_TERMINAL_WIDTH_FOR_STATUS = 40
_MIN_TERMINAL_WIDTH_FOR_BOTH = 90


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
        import os
        try:
            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80

    if terminal_width < _MIN_TERMINAL_WIDTH_FOR_LOGO:
        return _render_status_only(snapshot, terminal_width=terminal_width, color=color)

    raw_logo = load_logo(
        width=terminal_width,
        color=color,
        prefer_ascii=prefer_ascii,
        max_lines=COMPACT_HEADER_LINES,
    )
    logo_lines = raw_logo.split("\n") if raw_logo else []

    while len(logo_lines) < COMPACT_HEADER_LINES:
        logo_lines.append("")

    if snapshot is None or terminal_width < _MIN_TERMINAL_WIDTH_FOR_BOTH:
        return logo_lines[:COMPACT_HEADER_LINES]

    logo_width = _max_line_width(logo_lines)
    right_width = terminal_width - logo_width - 2
    if right_width < 20:
        return logo_lines[:COMPACT_HEADER_LINES]

    status_lines = format_status_compact_right(
        snapshot,
        color=bool(color),
        right_width=right_width,
    )

    while len(status_lines) < COMPACT_HEADER_LINES:
        status_lines.append("")

    result = []
    for i in range(COMPACT_HEADER_LINES):
        logo_part = logo_lines[i] if i < len(logo_lines) else ""
        status_part = status_lines[i] if i < len(status_lines) else ""
        padded_logo = _pad_to(logo_part, logo_width)
        separator = "  " if _visible_length(logo_part) > 0 else ""
        line = padded_logo + separator + status_part
        result.append(line)

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
