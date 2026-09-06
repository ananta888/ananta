"""Closed image assignment and receipt wire types; no decoder or Hub dependencies."""

import base64
import hashlib
import struct
from dataclasses import dataclass, field

from ananta_contracts.persona_assignment import ASSIGNMENT_FIELDS as ASSIGNMENT_FIELDS
from ananta_contracts.persona_assignment import validate_persona_assignment

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 7 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SanitizedPersonaImage:
    source_sha256: str
    image_sha256: str
    preview_sha256: str
    width: int
    height: int
    png: bytes = field(repr=False)
    preview: bytes = field(repr=False)


def validate_assignment(value, now):
    return validate_persona_assignment(value, now, schema="ananta.persona-image-task.v1")


def png_dimensions(content):
    if len(content) < 33 or content[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or content[24:26] != b"\x08\x06":
        raise ValueError("persona_image_header_invalid")
    return struct.unpack(">II", content[16:24])


def encode_image(image):
    return {
        "schema": "ananta.persona-image-inspection.v1",
        **{
            name: getattr(image, name)
            for name in ("source_sha256", "image_sha256", "preview_sha256", "width", "height")
        },
        "png": base64.b64encode(image.png).decode(),
        "preview": base64.b64encode(image.preview).decode(),
    }


def decode_image(value, source_sha256):
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "source_sha256",
        "image_sha256",
        "preview_sha256",
        "width",
        "height",
        "png",
        "preview",
    }:
        raise ValueError("persona_image_result_invalid")
    if value["schema"] != "ananta.persona-image-inspection.v1" or value["source_sha256"] != source_sha256:
        raise ValueError("persona_image_source_mismatch")
    payloads = {}
    for name, maximum, digest in (("png", MAX_INPUT_BYTES, "image_sha256"), ("preview", 350_000, "preview_sha256")):
        encoded = value[name]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((maximum + 2) // 3):
            raise ValueError("persona_image_result_size_invalid")
        content = base64.b64decode(encoded, validate=True)
        if not 0 < len(content) <= maximum or hashlib.sha256(content).hexdigest() != value[digest]:
            raise ValueError("persona_image_result_digest_invalid")
        payloads[name] = content
    if (
        any(type(value[key]) is not int or not 0 < value[key] <= 1024 for key in ("width", "height"))
        or png_dimensions(payloads["png"]) != (value["width"], value["height"])
        or any(not 0 < dimension <= 256 for dimension in png_dimensions(payloads["preview"]))
    ):
        raise ValueError("persona_image_result_dimensions_invalid")
    return SanitizedPersonaImage(
        source_sha256, value["image_sha256"], value["preview_sha256"], value["width"], value["height"], **payloads
    )
