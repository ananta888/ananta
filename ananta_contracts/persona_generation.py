"""Small closed procedural generation wire; no policy or evidence issuance."""

import base64
import hashlib
import json

from ananta_contracts.persona_assignment import validate_persona_assignment
from ananta_contracts.persona_image import png_dimensions

PROFILE = "procedural-avatar-v1"
MAX_OUTPUT = 500_000
RAW_VIDEO_BYTES = 24 * 256 * 256 * 3


def validate_recipe(value):
    if (
        type(value) is not dict
        or set(value) != {"profile", "media_kind", "palette"}
        or value["profile"] != PROFILE
        or value["media_kind"] not in ("image", "video")
        or value["palette"] not in ("indigo", "teal", "amber")
    ):
        raise ValueError("persona_generation_recipe_invalid")
    return value


def recipe_bytes(value):
    return json.dumps(validate_recipe(value), sort_keys=True, separators=(",", ":")).encode()


def recipe_digest(value):
    return hashlib.sha256(recipe_bytes(value)).hexdigest()


def validate_assignment(value, now):
    return validate_persona_assignment(value, now, schema="ananta.persona-generation-task.v1")


def decode_result(value, assignment, recipe):
    validate_recipe(recipe)
    expected_mime = "image/png" if recipe["media_kind"] == "image" else "video/mp4"
    if (
        type(value) is not dict
        or set(value) != {"task_id", "lease_id", "media_type", "content"}
        or value["task_id"] != assignment["task_id"]
        or value["lease_id"] != assignment["lease_id"]
        or value["media_type"] != expected_mime
        or type(value["content"]) is not str
        or not 24 <= len(value["content"]) <= 4 * ((MAX_OUTPUT + 2) // 3)
    ):
        raise ValueError("persona_generation_result_invalid")
    content = base64.b64decode(value["content"], validate=True)
    if not 16 <= len(content) <= MAX_OUTPUT:
        raise ValueError("persona_generation_result_size_invalid")
    if recipe["media_kind"] == "image":
        if png_dimensions(content) != (256, 256):
            raise ValueError("persona_generation_image_invalid")
    elif content[4:8] != b"ftyp":
        raise ValueError("persona_generation_video_invalid")
    return content


class GenerationWire:
    """Only the bounded signed server's transport configuration, not a decoder."""

    kind = "generation"
    path = "/v1/persona-generations"
    domain = b"persona-generation-v1"
    request_limit = 8192
    result_limit = 680_000
