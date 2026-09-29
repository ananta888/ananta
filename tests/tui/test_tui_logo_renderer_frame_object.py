from __future__ import annotations

from PIL import Image

from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame


def test_pixel_frame_from_image_png_and_cache_key() -> None:
    image = Image.new("RGBA", (4, 2), (10, 20, 30, 255))
    frame = PixelFrame.from_image(image, metadata={"renderer": "test"})

    assert frame.width_px == 4
    assert frame.height_px == 2
    assert frame.is_empty is False
    assert frame.to_png_bytes().startswith(b"\x89PNG")
    assert len(frame.cache_key()) == 64
