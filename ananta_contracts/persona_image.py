"""Closed image assignment and receipt wire types; no decoder or Hub dependencies."""

import base64
import hashlib
import re
import struct
from dataclasses import dataclass, field

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 7 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
ASSIGNMENT_FIELDS = {
    "schema",
    "task_id",
    "assignment_id",
    "lease_id",
    "tenant_id",
    "project_id",
    "run_id",
    "run_binding_digest",
    "admission_digest",
    "owner_subject",
    "deadline",
    "source_sha256",
    "evidence",
}


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
    if (
        not isinstance(value, dict)
        or set(value) != ASSIGNMENT_FIELDS
        or value["schema"] != "ananta.persona-image-task.v1"
    ):
        raise ValueError("persona_assignment_invalid")
    for name in ("task_id", "assignment_id", "lease_id", "tenant_id", "project_id", "owner_subject", "run_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError("persona_assignment_identity_invalid")
    for name in ("run_binding_digest", "admission_digest", "source_sha256"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[a-f0-9]{64}", value[name]):
            raise ValueError("persona_assignment_digest_invalid")
    if type(value["deadline"]) is not int or not now < value["deadline"] <= now + 20:
        raise ValueError("persona_assignment_expired")
    projection = validate_hub_evidence_assignment(value["evidence"])
    for name, projected in (
        ("task_id", "task_id"),
        ("assignment_id", "assignment_id"),
        ("lease_id", "dispatch_lease_id"),
        ("run_id", "run_id"),
        ("run_binding_digest", "binding_digest"),
    ):
        if value[name] != projection[projected]:
            raise ValueError("persona_assignment_projection_mismatch")
    return value


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
