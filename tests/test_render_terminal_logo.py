from __future__ import annotations

import pytest

from scripts.render_terminal_logo import (
    ASCII_PALETTES,
    render_ascii,
)


def _make_test_img(w: int, h: int, pixels: list[tuple[int, int, int, int]]):
    from PIL import Image
    img = Image.new("RGBA", (w, h))
    img.putdata(pixels)
    return img


def _white_pixel():
    return (255, 255, 255, 255)


def _black_pixel():
    return (0, 0, 0, 255)


def _red_pixel():
    return (255, 0, 0, 255)


def test_ascii_contains_only_ascii():
    w, h = 10, 10
    px = [_black_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    for ch in result:
        if ch != "\n":
            assert ord(ch) < 128, f"Non-ASCII char {ch!r} (ord={ord(ch)})"


def test_ascii_no_block_chars():
    w, h = 10, 10
    px = [_black_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    blocked = {"▀", "▄", "█", "▌", "▐"}
    for ch in result:
        assert ch not in blocked, f"Block char found: {ch!r}"


def test_ascii_no_ansi_escapes():
    w, h = 10, 10
    px = [_black_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    assert "\x1b" not in result


def test_white_background_becomes_spaces():
    w, h = 5, 5
    px = [_white_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    lines = result.split("\n")
    for line in lines:
        assert all(ch == " " for ch in line), f"Expected only spaces, got: {line!r}"


def _gradient_img(w: int, h: int):
    from PIL import Image
    img = Image.new("RGBA", (w, h))
    px = []
    for y in range(h):
        for x in range(w):
            v = int(255 * (x / max(w - 1, 1)) * (y / max(h - 1, 1)))
            px.append((v, v, v, 255))
    img.putdata(px)
    return img


def test_clean_palette_has_more_than_5_visible_chars():
    w, h = 20, 10
    img = _gradient_img(w, h)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    visible = {ch for ch in result if ch != "\n" and ch != " "}
    assert len(visible) > 5, f"Only {len(visible)} unique visible chars"


def test_detailed_palette_has_more_than_10_visible_chars():
    w, h = 20, 10
    img = _gradient_img(w, h)
    result = render_ascii(img, ASCII_PALETTES["detailed"])
    visible = {ch for ch in result if ch != "\n" and ch != " "}
    assert len(visible) > 10, f"Only {len(visible)} unique visible chars"


def test_red_pixel_not_white():
    w, h = 5, 5
    px = [_red_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"])
    lines = result.split("\n")
    all_space = all(ch == " " for line in lines for ch in line)
    assert not all_space, "Red pixels should produce visible chars"


def test_dither_does_not_crash():
    w, h = 10, 10
    px = [_black_pixel()] * (w * h)
    img = _make_test_img(w, h, px)
    result = render_ascii(img, ASCII_PALETTES["clean"], dither=True)
    assert len(result) > 0
    for ch in result:
        if ch != "\n":
            assert ord(ch) < 128
