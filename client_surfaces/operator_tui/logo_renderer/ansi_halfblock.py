from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _luma(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def _pixel_alpha(
    r: int,
    g: int,
    b: int,
    a: int,
    *,
    white_luma: int,
    falloff: int,
    alpha_cutoff: int,
) -> float:
    if a < alpha_cutoff:
        return 0.0
    lum = _luma(r, g, b)
    if lum >= white_luma:
        return 0.0
    if lum >= white_luma - falloff:
        return (white_luma - lum) / float(max(1, falloff))
    return 1.0


def _col_dist(a: tuple[int, int, int], b: tuple[int, int, int] | None) -> int:
    if b is None:
        return 10**9
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _ansi_fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def _ansi_bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"


def _ansi_reset() -> str:
    return "\x1b[0m"


def _extract_rgba(image: Any) -> tuple[int, int, Sequence[tuple[int, int, int, int]]]:
    if not hasattr(image, "size"):
        raise TypeError("image must provide size")
    width, height = image.size
    if hasattr(image, "get_flattened_data"):
        pixels = image.get_flattened_data()
    elif hasattr(image, "getdata"):
        pixels = image.getdata()
    else:
        raise TypeError("image must provide getdata() or get_flattened_data()")
    return int(width), int(height), list(pixels)


def render_halfblock_image(
    image: Any,
    *,
    visible_threshold: float = 0.3,
    white_luma: int = 240,
    falloff: int = 10,
    alpha_cutoff: int = 200,
    color_tolerance: int = 25,
    no_color: bool = False,
) -> list[str]:
    width, height, pixels = _extract_rgba(image)
    if width <= 0 or height <= 1:
        return []

    threshold = max(0.0, min(1.0, visible_threshold))

    def get_pixel(x: int, y: int) -> tuple[int, int, int, int]:
        return pixels[y * width + x]

    def is_transparent(p: tuple[int, int, int, int]) -> bool:
        alpha = _pixel_alpha(
            p[0],
            p[1],
            p[2],
            p[3],
            white_luma=white_luma,
            falloff=falloff,
            alpha_cutoff=alpha_cutoff,
        )
        return alpha < threshold

    lines: list[str] = []
    for y in range(0, height - 1, 2):
        if no_color:
            line = []
            for x in range(width):
                top = get_pixel(x, y)
                bottom = get_pixel(x, y + 1)
                top_t = is_transparent(top)
                bottom_t = is_transparent(bottom)
                if top_t and bottom_t:
                    line.append(" ")
                elif top_t and not bottom_t:
                    line.append("▄")
                elif not top_t and bottom_t:
                    line.append("▀")
                else:
                    line.append("▀" if _luma(*top[:3]) > _luma(*bottom[:3]) else "▄")
            lines.append("".join(line))
            continue

        parts: list[str] = []
        last_fg: tuple[int, int, int] | None = None
        last_bg: tuple[int, int, int] | None = None
        x = 0
        while x < width:
            tr, tg, tb, ta = get_pixel(x, y)
            br, bg, bb, ba = get_pixel(x, y + 1)
            top_t = is_transparent((tr, tg, tb, ta))
            bottom_t = is_transparent((br, bg, bb, ba))

            if top_t and bottom_t:
                if last_fg is not None or last_bg is not None:
                    parts.append(_ansi_reset())
                    last_fg = None
                    last_bg = None
                parts.append(" ")
                x += 1
                continue

            if top_t and not bottom_t:
                ch = "▄"
                fg = (br, bg, bb)
                bg_key = None
            elif not top_t and bottom_t:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = None
            else:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = (br, bg, bb)

            run = 1
            while x + run < width:
                ntr, ntg, ntb, nta = get_pixel(x + run, y)
                nbr, nbg, nbb, nba = get_pixel(x + run, y + 1)
                ntop_t = is_transparent((ntr, ntg, ntb, nta))
                nbottom_t = is_transparent((nbr, nbg, nbb, nba))
                if ntop_t and nbottom_t:
                    break
                if ntop_t and not nbottom_t:
                    if _col_dist((nbr, nbg, nbb), fg) <= color_tolerance and bg_key is None:
                        run += 1
                        continue
                elif not ntop_t and nbottom_t:
                    if _col_dist((ntr, ntg, ntb), fg) <= color_tolerance and bg_key is None:
                        run += 1
                        continue
                else:
                    if (
                        _col_dist((ntr, ntg, ntb), fg) <= color_tolerance
                        and _col_dist((nbr, nbg, nbb), bg_key) <= color_tolerance
                    ):
                        run += 1
                        continue
                break

            if fg != last_fg or bg_key != last_bg:
                if last_fg is not None or last_bg is not None:
                    parts.append(_ansi_reset())
                if bg_key is not None:
                    parts.append(_ansi_bg(*bg_key))
                parts.append(_ansi_fg(*fg))
                last_fg = fg
                last_bg = bg_key

            parts.append(ch * run)
            x += run

        if last_fg is not None or last_bg is not None:
            parts.append(_ansi_reset())
        lines.append("".join(parts))

    return lines


def render_halfblock_text(image: Any, **kwargs: Any) -> str:
    return "\n".join(render_halfblock_image(image, **kwargs))
