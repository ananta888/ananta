"""Optional immutable image assignment for the existing bounded Meet turn."""

import base64
import hashlib
import re

from ananta_contracts.persona_image import MAX_INPUT_BYTES, png_dimensions


def validate_reference(value):
    if not isinstance(value, dict) or set(value) != {
        "tenant_id",
        "project_id",
        "artifact_id",
        "revision",
        "sha256",
        "kind",
        "classification",
    }:
        raise ValueError("meet_persona_reference_invalid")
    for name in ("tenant_id", "project_id", "artifact_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError("meet_persona_reference_invalid")
    if (
        type(value["revision"]) is not int
        or value["revision"] != 1
        or value["kind"] != "image"
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        or value["classification"] not in ("production", "synthetic", "test_only")
    ):
        raise ValueError("meet_persona_reference_invalid")
    return value


def decode_assignment(value, *, tenant_id, project_id):
    if not isinstance(value, dict) or set(value) != {"reference", "png"}:
        raise ValueError("meet_persona_assignment_invalid")
    reference = validate_reference(value["reference"])
    if (reference["tenant_id"], reference["project_id"]) != (tenant_id, project_id):
        raise ValueError("meet_persona_scope_mismatch")
    if not isinstance(value["png"], str) or len(value["png"]) > 4 * ((MAX_INPUT_BYTES + 2) // 3):
        raise ValueError("meet_persona_image_too_large")
    content = base64.b64decode(value["png"], validate=True)
    if not 0 < len(content) <= MAX_INPUT_BYTES or hashlib.sha256(content).hexdigest() != reference["sha256"]:
        raise ValueError("meet_persona_image_digest_invalid")
    if any(not 0 < dimension <= 1024 for dimension in png_dimensions(content)):
        raise ValueError("meet_persona_image_dimensions_invalid")
    return content
