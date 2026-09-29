"""Deterministic in-memory pixels, not recorded media or release evidence."""

import io

import pytest
from PIL import Image

from worker.image_features import image_features


def encoded_image(size=(16, 9), color=(240, 20, 10), format="PNG"):
    with Image.new("RGB", size, color) as image, io.BytesIO() as output:
        image.save(output, format=format)
        return output.getvalue()


def test_statistics_preserve_existing_result_without_global_decoder_mutation():
    before = Image.MAX_IMAGE_PIXELS
    result = image_features(encoded_image((1280, 720)), max_pixels=20_000_000, thumbnail=(512, 512))
    assert result == {"width": 512, "height": 288, "mode": "RGB", "average_rgb": [240, 20, 10]}
    assert Image.MAX_IMAGE_PIXELS == before


def test_strict_pixel_budget_is_checked_before_decoding_or_thumbnail(monkeypatch):
    value = encoded_image((100, 100))
    load = Image.Image.load
    calls = []
    monkeypatch.setattr(Image.Image, "load", lambda self: (calls.append(True), load(self))[1])
    with pytest.raises(ValueError, match="pixels_exceeded"):
        image_features(value, max_pixels=9999, thumbnail=(1, 1))
    assert calls == []


@pytest.mark.parametrize("value", [b"", b"not an image", "image", None])
def test_invalid_content_is_not_interpreted_as_paths_or_urls(value):
    with pytest.raises((ValueError, OSError)):
        image_features(value, max_pixels=1000, thumbnail=(10, 10))


def test_profile_may_require_only_jpeg_without_upscaling_input():
    with pytest.raises(OSError):
        image_features(encoded_image(), max_pixels=230400, thumbnail=(640, 360), formats=("JPEG",))
    result = image_features(encoded_image(format="JPEG"), max_pixels=230400, thumbnail=(640, 360), formats=("JPEG",))
    assert (result["width"], result["height"]) == (16, 9)
    assert len(result["average_rgb"]) == 3
