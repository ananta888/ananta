"""Closed clip assignment; immutable bytes never confer publication authority."""

import re

from ananta_contracts.persona_video import decode_video


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
        raise ValueError("meet_persona_video_reference_invalid")
    for name in ("tenant_id", "project_id", "artifact_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError("meet_persona_video_reference_invalid")
    if (
        type(value["revision"]) is not int
        or value["revision"] != 1
        or value["kind"] != "video"
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        or value["classification"] not in ("production", "synthetic", "test_only")
    ):
        raise ValueError("meet_persona_video_reference_invalid")
    return value


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
