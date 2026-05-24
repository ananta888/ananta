from __future__ import annotations

import os
import re
import sys

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_ASSET_CACHE: dict[str, str | None] = {}


def _read_asset(filename: str) -> str | None:
    if filename not in _ASSET_CACHE:
        path = os.path.join(_ASSETS_DIR, filename)
        try:
            with open(path) as f:
                _ASSET_CACHE[filename] = f.read()
        except (FileNotFoundError, OSError):
            _ASSET_CACHE[filename] = None
    return _ASSET_CACHE[filename]


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _select_asset(
    width: int,
    color: bool,
    prefer_ascii: bool = False,
) -> str | None:
    if color and not prefer_ascii:
        if width >= 110:
            result = _read_asset("ansi_halfblock_120.txt")
            if result is not None:
                return result
        result = _read_asset("ansi_halfblock_90.txt")
        if result is not None:
            return result

    if color and prefer_ascii:
        if width >= 160:
            result = _read_asset("ascii_color_180.txt")
            if result is not None:
                return result
        result = _read_asset("ascii_color_90.txt")
        if result is not None:
            return result

    if width >= 160:
        result = _read_asset("ascii_fallback_180.txt")
        if result is not None:
            return result
    result = _read_asset("ascii_fallback_160.txt")
    if result is not None:
        return result
    result = _read_asset("mono_fallback_90.txt")
    if result is not None:
        return result

    return None


def load_logo(
    width: int | None = None,
    color: bool | None = None,
    prefer_ascii: bool = False,
    max_lines: int | None = None,
) -> str:
    if os.getenv("ANANTA_TUI_SPLASH", "").strip() == "0":
        return ""

    if width is None:
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80

    if color is None:
        if os.getenv("NO_COLOR"):
            color = False
        else:
            try:
                color = sys.stdout.isatty()
            except OSError:
                color = False

    raw = _select_asset(width=width, color=color, prefer_ascii=prefer_ascii)
    if raw is None:
        return ""

    if max_lines is not None:
        lines = raw.split("\n")
        trimmed = "\n".join(lines[:max_lines])
        return trimmed

    return raw


def logo_line_count(
    width: int | None = None,
    color: bool | None = None,
    prefer_ascii: bool = False,
) -> int:
    raw = load_logo(width=width, color=color, prefer_ascii=prefer_ascii)
    if not raw:
        return 0
    return len(raw.split("\n"))


def strip_ansi_from_logo(text: str) -> str:
    return _strip_ansi(text)


def clear_asset_cache() -> None:
    _ASSET_CACHE.clear()
