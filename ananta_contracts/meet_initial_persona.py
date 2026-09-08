"""Historical initial artwork/voice pins; fresh Hub authority still gates every output."""

import re

from ananta_contracts.meet_dialog_voice import content_digest
from ananta_contracts.persona_reference import validate_reference

SCHEMA = "ananta.meet-initial-persona.v1"


def initial_projection(selections):
    result = {"schema": SCHEMA}
    for name, selection in selections.items():
        result[name] = {
            "mode": selection["mode"],
            "reference": dict(selection["reference"]),
            "selection_digest": content_digest(selection),
        }
        if selection["mode"] == "persona-video-v1":
            result[name]["repeat_mode"] = selection["repeat_mode"]
    return result


def validate_initial_persona(value, tenant, project, *, avatar_images=False, avatar_videos=False, voice_profiles=False):
    error = "meet_initial_persona_invalid"
    if (
        type(value) is not dict
        or value.get("schema") != SCHEMA
        or not {"schema"} < set(value) <= {"schema", "avatar", "voice"}
    ):
        raise ValueError(error)
    for name in value.keys() - {"schema"}:
        row = value[name]
        if type(row) is not dict:
            raise ValueError(error)
        mode = row.get("mode")
        if name == "voice":
            kind, allowed = "voice", voice_profiles is True and mode == "persona-voice-v1"
        else:
            kind = "video" if mode == "persona-video-v1" else "image"
            allowed = avatar_images is True and (
                mode == "persona-image-v1" or avatar_videos is True and mode == "persona-video-v1"
            )
        fields = {"mode", "reference", "selection_digest"} | ({"repeat_mode"} if kind == "video" else set())
        if (
            not allowed
            or set(row) != fields
            or type(row["selection_digest"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", row["selection_digest"])
            or kind == "video"
            and row["repeat_mode"] not in ("loop", "hold_last")
        ):
            raise ValueError(error)
        ref = validate_reference(row["reference"], kind=kind, error=error)
        if (ref["tenant_id"], ref["project_id"]) != (tenant, project):
            raise ValueError(error)
    return value
