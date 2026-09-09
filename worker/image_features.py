"""Execution-only bounded image statistics; no storage, policy or decoder globals."""

import io


def image_features(content: bytes, *, max_pixels: int, thumbnail: tuple[int, int], formats=None, max_dimensions=None):
    from PIL import Image

    if not isinstance(content, bytes) or not content:
        raise ValueError("image_features_input_invalid")
    with Image.open(io.BytesIO(content), formats=formats) as image:
        if image.width * image.height > max_pixels:
            raise ValueError("image_features_pixels_exceeded")
        if max_dimensions is not None and (image.width > max_dimensions[0] or image.height > max_dimensions[1]):
            raise ValueError("image_features_dimensions_exceeded")
        image.verify()
    with Image.open(io.BytesIO(content), formats=formats) as image:
        image.thumbnail(thumbnail)
        with image.convert("RGB") as rgb:
            histogram = rgb.histogram()
            pixels = max(1, rgb.width * rgb.height)
            averages = [
                round(sum(index * histogram[channel * 256 + index] for index in range(256)) / pixels, 3)
                for channel in range(3)
            ]
        return {"width": image.width, "height": image.height, "mode": image.mode, "average_rgb": averages}
