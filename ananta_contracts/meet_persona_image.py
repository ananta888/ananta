"""Optional immutable image assignment for the existing bounded Meet turn."""

import base64
import hashlib

from ananta_contracts.persona_image import MAX_INPUT_BYTES, png_dimensions
from ananta_contracts.persona_reference import validate_reference as validate_asset_reference


def validate_reference(value):
    return validate_asset_reference(value, kind="image", error="meet_persona_reference_invalid")


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
