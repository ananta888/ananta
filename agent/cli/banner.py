from __future__ import annotations

import os

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

_MONO_90 = (
    "                                                                                          \n"
    "                                                                                          \n"
    "                                                                                          \n"
    "                                                                                          \n"
    "                                       ▀▀▀▀▀▀▀▀▀▀▀▀▄                                      \n"
    "                          ▄▄▄▄▄▄▄▄▄▄▄▄▀▄▀▀▀▀▀▀▀▀▀▀▄▀▀                                     \n"
    "                    ▄▄▀▀▀▄▄▄▄▀▀▀▀▀▄▀▄▀▀▄▄▄▄▀▄▀▄▀▄▄▄▀▄▀▄                                   \n"
    "                  ▄▀▄▄▀▀▀▀▀▀▀▀▄▀▀▀▀▀▀▀▀▀▀▀▀▄▄ ▀▄▀▄▄▄▀▄▀▀                                  \n"
    "                 ▀▄▄▀▀▄▄▄▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▄▀▀▀ ▄▄▄▄▄▄▄▄▄▄▄▄▄▀▀▀▀▀▀▄▄                       \n"
    "                 ▄▄▀▄▀▀▀▀▄▀▄▄▄▄▄▀▄▄▄▀▀▀▀▄▀▀▀▀▄▄▄▄▀▀▀▀▀▀▀▀▄▄▄▄▀▀▀▀▄▄▀▄                     \n"
    "                  ▀▄▄▀▀▀▀▄▄▀▄▄▀▀▄▄▄▄▄▄▄▄▀▀▀▀▀▄▄▄▄▀▀▀▀▀▀▀▀▀▀▀▀▄▄▀▀▄▀▀▄▀                    \n"
    "                   ▄▀▄▄▄▄▄▄▄▄▄▄▀▀▀▀▀▄▄▄▄▀▀▀▀▀▀▄▄▄▄▄▄▄▄▀▀▀▀▀▀▄▀▀▀▀▄▄▀▀                     \n"
    "                   ▄▄▀▀▀▀▀▀▄▄▄▄▀▀▀▀▀▀▀▄▄▄▄▄▄▀▀▀▀▀▀▀▀▄▄▄▄▄▄▄▄▄▀▀▀▀                         \n"
    "                     ▀▀▄▄▀▀▀▀▀▀▀▄▀▀▄▀▀▀▄▄▄▄▀▀▀▀▀▄▄▄▄▄▄▄▄▀▄▄▄▄▄▀▄▀▄                        \n"
    "                       ▄▀▄▀▄▄▄▄▄▀▄▀                     ▀▄▀▄▄▄▄▄▄▄▀▄                      \n"
    "                      ▀▄▄▄▄▄▄▄▄▄▄▀                       ▀▄▄▄▄▄▄▄▄▄▄▀                     \n"
    "                                                                                          \n"
    "                                                                                          \n"
    "                                                                                          \n"
    "                                                                                          \n"
    "                                                                                          \n"
)


def _read_asset(filename: str) -> str | None:
    path = os.path.join(_ASSETS_DIR, filename)
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def get_banner(
    width: int | None = None,
    color: bool | None = None,
    style: str = "auto",
) -> str:
    if style != "auto" and style not in ("large", "medium", "mono"):
        style = "auto"

    no_color = os.getenv("NO_COLOR") or os.getenv("ANANTA_NO_BANNER")
    if no_color:
        return ""

    if width is None:
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80

    if color is None:
        color = not bool(os.getenv("NO_COLOR"))

    if not color:
        return _MONO_90

    if style == "auto":
        if width >= 110:
            style = "large"
        elif width >= 80:
            style = "medium"
        else:
            style = "mono"

    if style == "mono":
        return _MONO_90

    if style == "large":
        ansi = _read_asset("ansi_halfblock_120.txt")
        if ansi is not None:
            return ansi

    ansi = _read_asset("ansi_halfblock_90.txt")
    if ansi is not None:
        return ansi

    return _MONO_90


def print_banner(
    width: int | None = None,
    color: bool | None = None,
    style: str = "auto",
) -> None:
    banner = get_banner(width=width, color=color, style=style)
    if banner:
        print(banner)
