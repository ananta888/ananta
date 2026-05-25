from __future__ import annotations

import re

from PIL import Image

from client_surfaces.operator_tui.logo_renderer.ansi_halfblock import (
    render_halfblock_image,
    render_halfblock_text,
)

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _image(w: int, h: int, pixels: list[tuple[int, int, int, int]]) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    img.putdata(pixels)
    return img


def test_halfblock_color_emits_ansi_and_reset():
    img = _image(1, 2, [(0, 180, 0, 255), (0, 90, 180, 255)])
    text = render_halfblock_text(img, no_color=False)
    assert "\x1b[38;2;" in text
    assert text.endswith("\x1b[0m")


def test_halfblock_no_color_has_no_ansi():
    img = _image(1, 2, [(0, 180, 0, 255), (0, 90, 180, 255)])
    text = render_halfblock_text(img, no_color=True)
    assert "\x1b" not in text
    assert text in {"▀", "▄"}


def test_halfblock_transparent_pair_renders_space():
    img = _image(1, 2, [(255, 255, 255, 0), (255, 255, 255, 0)])
    text = render_halfblock_text(img, no_color=True)
    assert text == " "


def test_halfblock_stable_height_and_width():
    pixels = [(0, 0, 0, 255)] * 16
    img = _image(4, 4, pixels)
    lines = render_halfblock_image(img, no_color=True)
    assert len(lines) == 2
    assert all(len(_ANSI_RE.sub("", line)) == 4 for line in lines)
