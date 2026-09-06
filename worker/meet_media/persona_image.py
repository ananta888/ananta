"""Bounded image normalization, executed only inside a supervised worker child."""

import hashlib
import io
from dataclasses import dataclass, field

from PIL import Image, ImageOps

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 2048 * 2048


@dataclass(frozen=True)
class SanitizedPersonaImage:
    source_sha256: str
    image_sha256: str
    preview_sha256: str
    width: int
    height: int
    png: bytes = field(repr=False)
    preview: bytes = field(repr=False)


def _encode(image, maximum):
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=6)
    value = output.getvalue()
    if len(value) > maximum:
        raise ValueError("persona_image_output_budget_exceeded")
    return value


def sanitize_image(content: bytes, media_type: str) -> SanitizedPersonaImage:
    """No origins, licensing or publication grants are inferred from pixels."""
    formats = {"image/png": "PNG", "image/jpeg": "JPEG"}
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES or media_type not in formats:
        raise ValueError("persona_image_input_invalid")
    try:
        with Image.open(io.BytesIO(content), formats=(formats[media_type],)) as source:
            if (
                source.format != formats[media_type]
                or not 0 < source.width <= 2048
                or not 0 < source.height <= 2048
                or source.width * source.height > MAX_PIXELS
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ValueError("invalid")
            source.verify()
        with Image.open(io.BytesIO(content), formats=(formats[media_type],)) as source:
            decoded = ImageOps.exif_transpose(source).convert("RGBA")
            # Start a new image from pixels: EXIF, comments, profiles, paths,
            # text chunks and other untrusted metadata never survive.
            cleaned = Image.frombytes("RGBA", decoded.size, decoded.tobytes())
        cleaned.thumbnail((1024, 1024))
        png = _encode(cleaned, MAX_INPUT_BYTES)
        preview_image = cleaned.copy()
        preview_image.thumbnail((256, 256))
        preview = _encode(preview_image, 350_000)
        return SanitizedPersonaImage(
            source_sha256=hashlib.sha256(content).hexdigest(),
            image_sha256=hashlib.sha256(png).hexdigest(),
            preview_sha256=hashlib.sha256(preview).hexdigest(),
            width=cleaned.width,
            height=cleaned.height,
            png=png,
            preview=preview,
        )
    except Exception:
        raise ValueError("persona_image_decode_failed") from None
