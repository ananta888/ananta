from __future__ import annotations

import pytest

from scripts.render_terminal_logo import (
    ASCII_PALETTES,
    RenderConfig,
    pixel_density,
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


# --- new tuning-parameter tests ---

def test_render_config_defaults():
    cfg = RenderConfig()
    assert cfg.white_luma == 240
    assert cfg.falloff == 10
    assert cfg.alpha_cutoff == 200
    assert cfg.visible_threshold == 0.3
    assert cfg.height_ratio == 0.48
    assert cfg.render_size == 800
    assert cfg.contrast == 1.0
    assert cfg.gamma == 1.0
    assert cfg.invert is False
    assert cfg.trim is False
    assert cfg.trim_padding == 2


def test_pixel_density_black():
    cfg = RenderConfig()
    d = pixel_density(0, 0, 0, 255, cfg)
    assert d == 1.0


def test_pixel_density_white():
    cfg = RenderConfig()
    d = pixel_density(255, 255, 255, 255, cfg)
    assert d == 0.0


def test_pixel_density_mid():
    cfg = RenderConfig()
    d = pixel_density(128, 128, 128, 255, cfg)
    assert 0.4 < d < 0.6


def test_pixel_density_with_contrast():
    cfg = RenderConfig(contrast=2.0)
    d = pixel_density(128, 128, 128, 255, cfg)
    assert 0.4 < d < 0.6


def test_pixel_density_with_gamma():
    cfg = RenderConfig(gamma=2.0)
    d = pixel_density(64, 64, 64, 255, cfg)
    # luma=64, density~0.749, 0.749^2~0.561
    assert 0.5 < d < 0.65


def test_pixel_density_invert():
    cfg = RenderConfig(invert=True)
    whiteish = pixel_density(200, 200, 200, 255, cfg)
    black = pixel_density(0, 0, 0, 255, cfg)
    assert whiteish > black


def test_pixel_density_transparent():
    cfg = RenderConfig()
    d = pixel_density(0, 0, 0, 0, cfg)
    assert d == 0.0


def test_white_luma_affects_visibility():
    cfg_low = RenderConfig(white_luma=200)
    cfg_high = RenderConfig(white_luma=250)
    d_low = pixel_density(220, 220, 220, 255, cfg_low)
    d_high = pixel_density(220, 220, 220, 255, cfg_high)
    assert d_low == 0.0
    assert d_high > 0.0


def test_height_ratio_changes_line_count(tmp_path):
    from PIL import Image
    from scripts.render_terminal_logo import load_image
    png = Image.new("RGBA", (100, 200), (0, 0, 0, 255))
    path = str(tmp_path / "test.png")
    png.save(path)

    img1 = load_image(path, 50, RenderConfig(height_ratio=0.5))
    img2 = load_image(path, 50, RenderConfig(height_ratio=1.0))
    assert img1.height != img2.height
    assert img2.height > img1.height


def test_trim_reduces_borders():
    from PIL import Image
    from scripts.render_terminal_logo import _trim_image
    w, h = 20, 20
    px = []
    for y in range(h):
        for x in range(w):
            if 4 <= x < 16 and 4 <= y < 16:
                px.append((0, 0, 0, 255))
            else:
                px.append((255, 255, 255, 255))
    img = Image.new("RGBA", (w, h))
    img.putdata(px)

    trimmed = _trim_image(img, RenderConfig(trim=True, trim_padding=0))
    assert trimmed.width == 12
    assert trimmed.height == 12


def test_trim_with_padding():
    from PIL import Image
    from scripts.render_terminal_logo import _trim_image
    w, h = 20, 20
    px = []
    for y in range(h):
        for x in range(w):
            if 4 <= x < 16 and 4 <= y < 16:
                px.append((0, 0, 0, 255))
            else:
                px.append((255, 255, 255, 255))
    img = Image.new("RGBA", (w, h))
    img.putdata(px)

    trimmed = _trim_image(img, RenderConfig(trim=True, trim_padding=2))
    assert trimmed.width == 16
    assert trimmed.height == 16


def test_trim_no_visible_pixel_returns_original():
    from PIL import Image
    from scripts.render_terminal_logo import _trim_image
    w, h = 10, 10
    px = [(255, 255, 255, 255)] * (w * h)
    img = Image.new("RGBA", (w, h))
    img.putdata(px)

    result = _trim_image(img, RenderConfig(trim=True))
    assert result.width == w
    assert result.height == h


def test_contrast_gamma_produce_only_ascii():
    from PIL import Image
    w, h = 10, 10
    px = []
    for y in range(h):
        for x in range(w):
            v = int(255 * x / (w - 1))
            px.append((v, v, v, 255))
    img = Image.new("RGBA", (w, h))
    img.putdata(px)

    cfg = RenderConfig(contrast=1.5, gamma=0.8)
    result = render_ascii(img, ASCII_PALETTES["clean"], cfg=cfg)
    for ch in result:
        if ch != "\n":
            assert ord(ch) < 128
    assert "\x1b" not in result
    blocked = {"▀", "▄", "█", "▌", "▐"}
    for ch in result:
        assert ch not in blocked


def test_ascii_custom_chars_stays_ascii():
    from PIL import Image
    w, h = 10, 10
    px = [(64, 64, 64, 255)] * (w * h)
    img = Image.new("RGBA", (w, h))
    img.putdata(px)

    result = render_ascii(img, " .-:=+*#%@")
    for ch in result:
        if ch != "\n":
            assert ord(ch) < 128
