"""Closed clip assignment; immutable bytes never confer publication authority."""

from ananta_contracts.persona_reference import validate_reference as validate_asset_reference
from ananta_contracts.persona_video import decode_video


def validate_reference(value):
    return validate_asset_reference(value, kind="video", error="meet_persona_video_reference_invalid")


def decode_assignment(value, *, tenant_id, project_id):
    if not isinstance(value, dict) or set(value) != {"reference", "clip", "origin_kind", "repeat_mode"}:
        raise ValueError("meet_persona_video_assignment_invalid")
    reference = validate_reference(value["reference"])
    if (reference["tenant_id"], reference["project_id"]) != (tenant_id, project_id):
        raise ValueError("meet_persona_video_scope_mismatch")
    if value["repeat_mode"] not in ("loop", "hold_last"):
        raise ValueError("meet_persona_video_repeat_mode_required")
    if value["origin_kind"] not in ("upload", "licensed_pack", "generated") or (
        value["origin_kind"] == "generated" and reference["classification"] == "production"
    ):
        raise ValueError("meet_persona_video_origin_invalid")
    clip = value["clip"]
    if not isinstance(clip, dict):
        raise ValueError("meet_persona_video_assignment_invalid")
    inspected = decode_video(clip, clip.get("source_sha256"))
    if inspected.video_sha256 != reference["sha256"]:
        raise ValueError("meet_persona_video_digest_mismatch")
    return inspected
