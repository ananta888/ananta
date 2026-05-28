from __future__ import annotations

_THUMB = "█"
_TRACK = "░"
_THUMB_ASCII = "|"
_TRACK_ASCII = "."
_TOP_ARROW = "▲"
_BOT_ARROW = "▼"
_TOP_ASCII = "^"
_BOT_ASCII = "v"


def render_scrollbar_column(
    *,
    content_height: int,
    viewport_height: int,
    offset: int,
    height: int,
    ascii_fallback: bool = False,
) -> list[str]:
    """Return `height` single-character strings for a vertical scrollbar column.

    Returns a list of spaces when content fits without scrolling (no scrollbar needed).
    """
    if content_height <= viewport_height or height <= 0:
        return [" "] * height

    h = max(2, height)
    thumb_char = _THUMB_ASCII if ascii_fallback else _THUMB
    track_char = _TRACK_ASCII if ascii_fallback else _TRACK
    top_char = _TOP_ASCII if ascii_fallback else _TOP_ARROW
    bot_char = _BOT_ASCII if ascii_fallback else _BOT_ARROW

    max_scroll = max(1, content_height - viewport_height)
    track_h = max(1, h - 2)
    thumb_h = max(1, round(track_h * viewport_height / max(1, content_height)))
    thumb_h = min(thumb_h, track_h)
    thumb_pos = round((track_h - thumb_h) * min(offset, max_scroll) / max_scroll)

    result: list[str] = [top_char]
    for i in range(track_h):
        if thumb_pos <= i < thumb_pos + thumb_h:
            result.append(thumb_char)
        else:
            result.append(track_char)
    result.append(bot_char)
    return result[:h]


def scrollbar_thumb_info(
    *,
    content_height: int,
    viewport_height: int,
    offset: int,
) -> dict[str, int]:
    """Return thumb position/size metadata for tests and diagnostics."""
    max_scroll = max(0, content_height - viewport_height)
    if max_scroll == 0:
        return {"thumb_pos": 0, "thumb_h": 1, "max_scroll": 0}
    track_h = max(1, viewport_height)
    thumb_h = max(1, round(track_h * viewport_height / max(1, content_height)))
    thumb_h = min(thumb_h, track_h)
    thumb_pos = round((track_h - thumb_h) * min(offset, max_scroll) / max_scroll)
    return {"thumb_pos": thumb_pos, "thumb_h": thumb_h, "max_scroll": max_scroll}


def minimal_scroll_indicator(*, offset: int, max_scroll: int) -> str:
    """One-line compact text indicator for tight spaces: e.g. '▲3 ▼12'."""
    if max_scroll <= 0:
        return ""
    above = offset
    below = max(0, max_scroll - offset)
    parts: list[str] = []
    if above > 0:
        parts.append(f"▲{above}")
    if below > 0:
        parts.append(f"▼{below}")
    return " ".join(parts)
