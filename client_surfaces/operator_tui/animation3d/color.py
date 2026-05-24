from __future__ import annotations

_ANSI_RESET = "\x1b[0m"


def _ansi_fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def _ansi_bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"


def parse_color_spec(spec: str) -> tuple[int, int, int] | None:
    if not spec:
        return None
    parts = spec.split(",")
    if len(parts) == 3:
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, TypeError):
            pass
    named = {
        "green": (0, 180, 80),
        "red": (200, 40, 40),
        "yellow": (200, 180, 40),
        "cyan": (40, 180, 200),
        "white": (200, 200, 200),
        "orange": (220, 120, 30),
        "dark_green": (0, 120, 60),
    }
    return named.get(spec.lower())


def render_colored(text: str, fg: tuple[int, int, int] | None, bg: tuple[int, int, int] | None) -> str:
    parts: list[str] = []
    if bg:
        parts.append(_ansi_bg(*bg))
    if fg:
        parts.append(_ansi_fg(*fg))
    parts.append(text)
    parts.append(_ANSI_RESET)
    return "".join(parts)
